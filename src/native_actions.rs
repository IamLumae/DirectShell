//! Semantic operations on a selected application, never on the user's input focus.
use windows::core::{BSTR, Interface, VARIANT};
use windows::Win32::Foundation::HWND;
use windows::Win32::UI::Accessibility::*;

#[derive(Default, Debug)]
pub struct AgentSelection {
    pub window: isize,
    pub name: String,
    pub replace_all: bool,
}

impl AgentSelection {
    pub fn select(&mut self, window: isize, name: &str) {
        self.window = window;
        self.name = name.to_string();
        self.replace_all = false;
    }
    pub fn target(&self, window: isize) -> Result<&str, String> {
        if self.window != window || self.name.is_empty() {
            Err("NO_AGENT_TARGET: select a named input with ds_click or ds_text first".into())
        } else { Ok(&self.name) }
    }
    pub fn insert(&self, current: &str, text: &str) -> String {
        if self.replace_all { text.to_string() } else { format!("{current}{text}") }
    }
}

fn api_error(error: windows::core::Error) -> String {
    format!("UIA_PROVIDER_ERROR: HRESULT 0x{:08X}; refresh state before any retry", error.code().0 as u32)
}

unsafe fn msaa_target(window: HWND, name:&str, role:u32, require_action:bool) -> Result<(IAccessible,VARIANT),String> {
    let mut raw=std::ptr::null_mut();
    AccessibleObjectFromWindow(window,0xFFFFFFFC,&IAccessible::IID,&mut raw).map_err(api_error)?;
    if raw.is_null(){return Err("MSAA_SERVER_UNAVAILABLE".into());}
    let root=IAccessible::from_raw(raw);
    let mut pending=vec![(root,VARIANT::from(0i32),0)];
    let mut matches=Vec::new();let mut visited=0;
    let started=std::time::Instant::now();
    while let Some((node,child,depth))=pending.pop() {
        visited+=1;
        if visited>4000||depth>64||started.elapsed().as_secs()>3{return Err("MSAA_SCAN_LIMIT: narrow target; no action performed".into());}
        let state=u32::try_from(&node.get_accState(&child).map_err(api_error)?).map_err(api_error)?;
        // Actions resolve visible controls only: do not walk thousands of
        // offscreen messages or hidden virtualized branches of a chat.
        if state & (0x20000000|1|0x8000|0x10000)!=0 {continue;}
        let found_name=node.get_accName(&child).map(|s|s.to_string()).unwrap_or_default();
        if found_name==name && u32::try_from(&node.get_accRole(&child).map_err(api_error)?).ok()==Some(role) {
            if !require_action||!node.get_accDefaultAction(&child).map_err(api_error)?.to_string().trim().is_empty(){matches.push((node.clone(),child.clone()));}
        }
        if i32::try_from(&child).ok()!=Some(0){continue;}
        let count=node.accChildCount().map_err(api_error)?;
        if count<0||count>4000{return Err("MSAA_SCAN_LIMIT".into());}
        let mut children=vec![VARIANT::new();count as usize];let mut obtained=0;
        AccessibleChildren(&node,0,&mut children,&mut obtained).map_err(api_error)?;
        for child in children.into_iter().take(obtained as usize) {
            if child.as_raw().Anonymous.Anonymous.vt==9 {
                let pointer=child.as_raw().Anonymous.Anonymous.Anonymous.pdispVal;
                if let Some(dispatch)=windows::Win32::System::Com::IDispatch::from_raw_borrowed(&pointer) {
                    if let Ok(accessible)=dispatch.cast::<IAccessible>(){pending.push((accessible,VARIANT::from(0i32),depth+1));}
                }
            } else if i32::try_from(&child).is_ok() {pending.push((node.clone(),child,depth+1));}
        }
    }
    if matches.len()!=1{return Err(format!("MSAA_TARGET_NOT_UNIQUE: {} semantic matches; no action performed",matches.len()));}
    Ok(matches.remove(0))
}

unsafe fn legacy_action(window: HWND, name:&str, pattern: &IUIAutomationLegacyIAccessiblePattern) -> Result<String,String> {
    // A native MSAA server can perform its semantic action directly. The UIA
    // proxy may expose the name but reject DoDefaultAction (UIA_E_NOTSUPPORTED).
    // Choose the route BEFORE mutating; never replay after an ambiguous error.
    if let Ok(accessible)=pattern.GetIAccessible() {
        let child=pattern.CurrentChildId().map_err(|e|format!("Legacy.ChildId: {}",api_error(e)))?;
        accessible.accDoDefaultAction(&VARIANT::from(child)).map_err(|e|format!("MSAA.accDoDefaultAction: {}",api_error(e)))?;
        return Ok("msaa_default_action_acknowledged".into());
    }
    let role=pattern.CurrentRole().map_err(api_error)?;
    // Chromium's UIA bridge may not hand out its native IAccessible object.
    // Resolve the same unique name+role inside the selected HWND's MSAA tree.
    let (accessible,child)=msaa_target(window,name,role,true)?;
    if let Ok(provider)=accessible.cast::<IRawElementProviderSimple>() {
        if let Ok(invoke)=provider.GetPatternProvider(UIA_InvokePatternId).and_then(|p|p.cast::<IInvokeProvider>()) {
            invoke.Invoke().map_err(|e|format!("Native IInvokeProvider.Invoke: {}",api_error(e)))?;
            return Ok("native_provider_invoke_acknowledged".into());
        }
    }
    accessible.accDoDefaultAction(&child).map_err(|e|format!("MSAA.accDoDefaultAction: {}",api_error(e)))?;
    Ok("msaa_default_action_acknowledged".into())
}

unsafe fn native_scroll(window:HWND,target:&IUIAutomationElement,direction:&str)->Result<String,String>{
    let legacy=target.GetCurrentPattern(UIA_LegacyIAccessiblePatternId).and_then(|p|p.cast::<IUIAutomationLegacyIAccessiblePattern>()).map_err(api_error)?;
    let name=target.CurrentName().map_err(api_error)?.to_string();
    let direct=legacy.GetIAccessible().ok().and_then(|acc|{
        let child=VARIANT::from(legacy.CurrentChildId().ok()?);
        if acc.get_accName(&child).ok()?.to_string()==name{Some((acc,child))}else{None}
    });
    let (mut node,child)=match direct{Some(v)=>v,None=>msaa_target(window,&name,legacy.CurrentRole().map_err(api_error)?,false)?};
    if i32::try_from(&child).ok()!=Some(0){node=node.get_accChild(&child).and_then(|p|p.cast::<IAccessible>()).map_err(api_error)?;}
    if node.get_accName(&VARIANT::from(0i32)).map_err(api_error)?.to_string()!=name{return Err("NATIVE_SCROLL_REFERENCE_MISMATCH".into());}
    let vertical=matches!(direction,"up"|"down");let forward=matches!(direction,"down"|"right");
    for _ in 0..32{
        if let Ok(provider)=node.cast::<IRawElementProviderSimple>().or_else(|_|node.cast::<windows::Win32::System::Com::IServiceProvider>().and_then(|s|s.QueryService::<IRawElementProviderSimple>(&IAccessible::IID))) {
            if let Ok(scroll)=provider.GetPatternProvider(UIA_ScrollPatternId).and_then(|p|p.cast::<IScrollProvider>()) {
                let supported=if vertical{scroll.VerticallyScrollable()}else{scroll.HorizontallyScrollable()}.map_err(api_error)?.as_bool();
                if supported {
                    let position=||if vertical{scroll.VerticalScrollPercent()}else{scroll.HorizontalScrollPercent()}.map_err(api_error);
                    let before=position()?;
                    if (forward&&before>=100.0)||(!forward&&before<=0.0){return Ok("scroll_boundary_reached".into());}
                    // Ask the provider to advance its own viewport. Older Chromium
                    // providers implement SetScrollPercent with incompatible scaling;
                    // relative Scroll also avoids stale virtualized-content extents.
                    let amount=if forward{ScrollAmount_LargeIncrement}else{ScrollAmount_LargeDecrement};
                    scroll.Scroll(if vertical{ScrollAmount_NoAmount}else{amount},if vertical{amount}else{ScrollAmount_NoAmount}).map_err(api_error)?;
                    let deadline=std::time::Instant::now()+std::time::Duration::from_secs(10);
                    loop {
                        let after=position()?;let change=after-before;
                        if (forward&&change>0.001)||(!forward&&change< -0.001){break;}
                        if std::time::Instant::now()>=deadline{return Err(format!("NATIVE_SCROLL_UNCONFIRMED: before={before:.6}, direction={direction}, observed={after:.6}; do not repeat"));}
                        std::thread::sleep(std::time::Duration::from_millis(50));
                    }
                    return Ok("native_provider_scroll_verified".into());
                }
            }
        }
        node=node.accParent().and_then(|p|p.cast::<IAccessible>()).map_err(|_|"SCROLL_PATTERN_UNAVAILABLE: native accessibility provider exposes no scroll operation".to_string())?;
    }
    Err("SCROLL_PATTERN_UNAVAILABLE".into())
}

pub unsafe fn scroll_once(uia:&IUIAutomation,window:HWND,direction:&str,name:&str)->Result<String,String>{
    if !matches!(direction,"up"|"down"|"left"|"right"){return Err("SCROLL_DIRECTION_INVALID".into());}
    let root=uia.ElementFromHandle(window).map_err(api_error)?;
    if let Some(outcome)=scroll_item_once(uia,window,direction,name)?{return Ok(outcome);}
    native_scroll(window,&element(uia,&root,name,"scroll")?,direction)
}

fn adjacent_offscreen(visible:&[bool],forward:bool)->Option<usize>{
    if forward{visible.iter().rposition(|v|*v).and_then(|i|(i+1<visible.len()).then_some(i+1))}
    else{visible.iter().position(|v|*v).and_then(|i|i.checked_sub(1))}
}

/// Virtualized lists can defer Scroll/SetScrollPercent while backgrounded.
/// Prefer the provider's semantic ScrollIntoView for the adjacent hidden item.
/// None means no mutation was attempted; after mutation, never fall back/replay.
unsafe fn scroll_item_once(uia:&IUIAutomation,window:HWND,direction:&str,name:&str)->Result<Option<String>,String>{
    if !matches!(direction,"up"|"down"){return Ok(None);}
    let root=uia.ElementFromHandle(window).map_err(api_error)?;
    let list=element(uia,&root,name,"scroll")?;
    let role=list.CurrentControlType().map_err(api_error)?;
    if role!=UIA_ListControlTypeId&&role!=UIA_TreeControlTypeId{return Ok(None);}
    let walker=uia.RawViewWalker().map_err(api_error)?;
    let mut children=Vec::new();let mut next=walker.GetFirstChildElement(&list);
    while let Ok(child)=next{next=walker.GetNextSiblingElement(&child);children.push(child);if children.len()>2000{return Err("SCROLL_ITEM_SCAN_LIMIT".into());}}
    let visible=children.iter().map(|c|c.CurrentIsOffscreen().map(|v|!v.as_bool()).map_err(api_error)).collect::<Result<Vec<_>,_>>()?;
    let Some(index)=adjacent_offscreen(&visible,direction=="down") else{return Ok(None);};
    let child=&children[index];
    if child.CurrentIsPassword().map_err(api_error)?.as_bool(){return Err("PROTECTED_SCROLL_ITEM".into());}
    let Ok(pattern)=child.GetCurrentPattern(UIA_ScrollItemPatternId).and_then(|p|p.cast::<IUIAutomationScrollItemPattern>()) else{return Ok(None);};
    pattern.ScrollIntoView().map_err(api_error)?;
    let deadline=std::time::Instant::now()+std::time::Duration::from_secs(3);
    loop{
        if !child.CurrentIsOffscreen().map_err(|e|format!("SCROLL_ITEM_READBACK_FAILED: {}; do not repeat",api_error(e)))?.as_bool(){return Ok(Some("scroll_item_visible_verified".into()));}
        if std::time::Instant::now()>=deadline{return Err("SCROLL_ITEM_UNCONFIRMED: selected item remains offscreen; do not repeat".into());}
        std::thread::sleep(std::time::Duration::from_millis(25));
    }
}

/// Exact and unique target. Never select the first of several equally named controls.
unsafe fn element(uia: &IUIAutomation, root: &IUIAutomationElement, name: &str, intent: &str) -> Result<IUIAutomationElement, String> {
    let (expected_id,name)=if let Some(encoded)=name.strip_prefix("@runtime[") {let (id,label)=encoded.split_once("]:").ok_or("Invalid runtime target")?;(Some(id),label)}else{(None,name)};
    if name.is_empty() { return Err("TARGET_REQUIRED: use a name from ds_update_view".into()); }
    let condition = uia.CreatePropertyCondition(UIA_NamePropertyId, &VARIANT::from(BSTR::from(name))).map_err(api_error)?;
    let matches = root.FindAll(TreeScope_Descendants, &condition).map_err(api_error)?;
    let count = matches.Length().map_err(api_error)?;
    let mut controls = Vec::new();
    for index in 0..count {
        let candidate = matches.GetElement(index).map_err(api_error)?;
        if let Some(id)=expected_id {if crate::window_query::runtime_id(&candidate)?!=id{continue;}}
        let kind = candidate.CurrentControlType().map_err(api_error)?;
        // Real Explorer exposes both a MenuItem and its TextBlock with the
        // same Name. A decorative label is not a second actionable target.
        // Keep genuine duplicate controls ambiguous; never pick the first.
        if can_be_action_target(kind,intent) { controls.push(candidate); }
    }
    // WinUI menu controls can wrap a same-named Button in a MenuItem. Resolve
    // that actual ancestry, not screen coordinates or arbitrary first matches.
    if controls.len() > 1 {
        let walker = uia.RawViewWalker().map_err(api_error)?;
        let mut ancestors = vec![false;controls.len()];
        for (child_index,child) in controls.iter().enumerate() {
            let mut node = child.clone();
            for _ in 0..32 {
                let Ok(parent) = walker.GetParentElement(&node) else { break; };
                if uia.CompareElements(&parent,root).map_err(api_error)?.as_bool() { break; }
                for (index,candidate) in controls.iter().enumerate() {
                    if index != child_index && uia.CompareElements(&parent,candidate).map_err(api_error)?.as_bool() { ancestors[index]=true; }
                }
                node=parent;
            }
        }
        controls=controls.into_iter().enumerate().filter_map(|(index,control)| if ancestors[index] {None} else {Some(control)}).collect();
    }
    if controls.len() != 1 { return Err(format!("TARGET_NOT_UNIQUE: {} matching controls ({count} named elements); refresh or use a more precise target", controls.len())); }
    let target = controls.remove(0);
    if !target.CurrentIsEnabled().map_err(api_error)?.as_bool() { return Err("TARGET_DISABLED".into()); }
    Ok(target)
}

fn can_be_action_target(kind: UIA_CONTROLTYPE_ID, intent: &str) -> bool {
    if matches!(intent,"text"|"type") { return is_text_control(kind); }
    ![UIA_TextControlTypeId, UIA_SeparatorControlTypeId, UIA_WindowControlTypeId,
        UIA_PaneControlTypeId,UIA_GroupControlTypeId,UIA_TitleBarControlTypeId].contains(&kind)
}

fn is_text_control(control_type: UIA_CONTROLTYPE_ID) -> bool {
    [UIA_EditControlTypeId, UIA_DocumentControlTypeId, UIA_ComboBoxControlTypeId].contains(&control_type)
}

unsafe fn value_pattern(target: &IUIAutomationElement) -> Result<IUIAutomationValuePattern, String> {
    // Chromium can expose ValuePattern on a checkbox. Pattern availability alone
    // does not make it an editable text field (the live Electron regression).
    if !is_text_control(target.CurrentControlType().map_err(api_error)?) {
        return Err("PATTERN_UNAVAILABLE: target is not a semantic text control".into());
    }
    let pattern = target.GetCurrentPattern(UIA_ValuePatternId)
        .and_then(|p| p.cast::<IUIAutomationValuePattern>())
        .map_err(|_| "PATTERN_UNAVAILABLE: target has no writable ValuePattern; no physical-input fallback".to_string())?;
    if pattern.CurrentIsReadOnly().map_err(api_error)?.as_bool() { return Err("TARGET_READ_ONLY".into()); }
    Ok(pattern)
}

/// Providers may acknowledge before their asynchronous accessibility cache updates.
/// Only repeat READS, never the mutation. Mismatch remains an explicit error.
fn verify_value(mut read: impl FnMut() -> Result<String, String>, expected: &str,
    wait: impl FnMut()) -> Result<(), String> {
    verify_observation(|| Ok(read()? == expected), "VALUE_READBACK_MISMATCH", wait)
}

fn verify_observation(mut observed: impl FnMut() -> Result<bool, String>, label: &str,
    mut wait: impl FnMut()) -> Result<(), String> {
    for attempt in 0..26 {
        if observed()? { return Ok(()); }
        if attempt < 25 { wait(); }
    }
    Err(format!("{label}: expected state not observed after bounded readback; do not blindly repeat"))
}

fn settle() { std::thread::sleep(std::time::Duration::from_millis(20)); }


unsafe fn fresh_scroll_percent(client:&IUIAutomation,window:HWND,id:&str,vertical:bool,name:&str)->Result<f64,String>{
    // Some Chromium/MSAA proxies retain the pre-action pattern snapshot.
    // Reacquire the same runtime identity, never repeat the scroll itself.
    let root=client.ElementFromHandle(window).map_err(api_error)?;
    if !name.is_empty(){
        let mut node=element(&client,&root,name,"scroll")?;
        let walker=client.RawViewWalker().map_err(api_error)?;
        for _ in 0..32 {
            if crate::window_query::runtime_id(&node)?==id {
                let pattern=node.GetCurrentPattern(UIA_ScrollPatternId).and_then(|p|p.cast::<IUIAutomationScrollPattern>()).map_err(api_error)?;
                return if vertical{pattern.CurrentVerticalScrollPercent()}else{pattern.CurrentHorizontalScrollPercent()}.map_err(api_error);
            }
            if client.CompareElements(&node,&root).map_err(api_error)?.as_bool(){break;}
            node=walker.GetParentElement(&node).map_err(api_error)?;
        }
        return Err("SCROLL_CONTAINER_NOT_FOUND_IN_SELECTED_ANCESTRY".into());
    }
    let condition=client.CreatePropertyCondition(UIA_IsScrollPatternAvailablePropertyId,&VARIANT::from(true)).map_err(api_error)?;
    let rows=root.FindAll(TreeScope_Subtree,&condition).map_err(api_error)?;
    for i in 0..rows.Length().map_err(api_error)?.min(256){
        let el=rows.GetElement(i).map_err(api_error)?;
        if crate::window_query::runtime_id(&el)?==id {
            let p=el.GetCurrentPattern(UIA_ScrollPatternId).and_then(|p|p.cast::<IUIAutomationScrollPattern>()).map_err(api_error)?;
            return if vertical{p.CurrentVerticalScrollPercent()}else{p.CurrentHorizontalScrollPercent()}.map_err(api_error);
        }
    }
    Err("SCROLL_CONTAINER_NOT_FOUND".into())
}

fn provider_text(value: &str, kind: UIA_CONTROLTYPE_ID) -> String {
    // RichEdit's Document ValuePattern returns CR paragraph separators even
    // for a CRLF document. Compare text semantics, never rewrite the payload.
    if kind == UIA_DocumentControlTypeId { value.replace("\r\n","\n").replace('\r',"\n") }
    else { value.to_string() }
}

/// A provider acknowledgement is not a workflow-completion claim: caller refreshes UI state.
pub unsafe fn execute(window: HWND, action: &str, text: &str, name: &str, selection: &mut AgentSelection) -> Result<String, String> {
    let uia = crate::automation_client::create().map_err(api_error)?;
    execute_on(&uia,window,action,text,name,selection)
}

pub unsafe fn execute_on(uia:&IUIAutomation,window: HWND, action: &str, text: &str, name: &str, selection: &mut AgentSelection) -> Result<String, String> {
    if let Ok(uia6) = uia.cast::<IUIAutomation6>() { uia6.SetConnectionTimeout(2000).map_err(api_error)?; }
    let root = uia.ElementFromHandle(window).map_err(api_error)?;
    let key = window.0 as isize;
    match action {
        "text" | "type" => {
            let target_name = if name.is_empty() { selection.target(key)? } else { name };
            let target = element(&uia, &root, target_name, action)?;
            let pattern = value_pattern(&target)?;
            let current = pattern.CurrentValue().map_err(api_error)?.to_string();
            let next = if action == "text" { text.to_string() } else { selection.insert(&current, text) };
            pattern.SetValue(&BSTR::from(next.as_str())).map_err(api_error)?;
            let kind=target.CurrentControlType().map_err(api_error)?;
            verify_value(|| pattern.CurrentValue().map(|v| provider_text(&v.to_string(),kind)).map_err(api_error), &provider_text(&next,kind),
                || std::thread::sleep(std::time::Duration::from_millis(20)))?;
            let selected_name = target_name.to_string();
            selection.select(key, &selected_name);
            Ok("value_verified".into())
        }
        "click" => {
            let target = element(&uia, &root, name, "click")?;
            // Menu/popover buttons (including Discord Inbox) expose
            // ExpandCollapse instead of Invoke; this is not combo-box-only.
            {
                if let Ok(pattern) = target.GetCurrentPattern(UIA_ExpandCollapsePatternId).and_then(|p| p.cast::<IUIAutomationExpandCollapsePattern>()) {
                    let before=pattern.CurrentExpandCollapseState().map_err(api_error)?;
                    let expected=if before==ExpandCollapseState_Expanded{ExpandCollapseState_Collapsed}else{ExpandCollapseState_Expanded};
                    if expected==ExpandCollapseState_Expanded{pattern.Expand()}else{pattern.Collapse()}.map_err(api_error)?;
                    verify_observation(|| Ok(pattern.CurrentExpandCollapseState().map_err(api_error)? == expected), "EXPAND_UNCONFIRMED", settle)?;
                    selection.select(key,name);
                    return Ok(if expected==ExpandCollapseState_Expanded{"expanded_verified"}else{"collapsed_verified"}.into());
                }
            }
            // Clicking an editable field selects it FOR THE AGENT, not for Windows.
            if value_pattern(&target).is_ok() {
                selection.select(key, name);
                return Ok("agent_target_selected".into());
            }
            if let Ok(pattern) = target.GetCurrentPattern(UIA_TogglePatternId).and_then(|p| p.cast::<IUIAutomationTogglePattern>()) {
                let before = pattern.CurrentToggleState().map_err(|e|format!("TogglePattern.CurrentToggleState: {}",api_error(e)))?;
                pattern.Toggle().map_err(|e|format!("TogglePattern.Toggle: {}",api_error(e)))?;
                verify_observation(|| Ok(pattern.CurrentToggleState().map_err(api_error)? != before), "TOGGLE_READBACK_UNCHANGED", settle)?;
                return Ok("toggle_verified".into());
            }
            if let Ok(pattern) = target.GetCurrentPattern(UIA_SelectionItemPatternId).and_then(|p| p.cast::<IUIAutomationSelectionItemPattern>()) {
                pattern.Select().map_err(|e|format!("SelectionItemPattern.Select: {}",api_error(e)))?;
                verify_observation(|| Ok(pattern.CurrentIsSelected().map_err(api_error)?.as_bool()), "SELECTION_UNCONFIRMED", settle)?;
                selection.select(key,name);
                return Ok("selection_verified".into());
            }
            if let Ok(pattern) = target.GetCurrentPattern(UIA_InvokePatternId).and_then(|p| p.cast::<IUIAutomationInvokePattern>()) {
                pattern.Invoke().map_err(|e|format!("InvokePattern.Invoke: {}",api_error(e)))?;
                return Ok("invoke_acknowledged".into());
            }
            if let Ok(pattern)=target.GetCurrentPattern(UIA_LegacyIAccessiblePatternId).and_then(|p|p.cast::<IUIAutomationLegacyIAccessiblePattern>()) {
                if !pattern.CurrentDefaultAction().map_err(|e|format!("LegacyIAccessiblePattern.CurrentDefaultAction: {}",api_error(e)))?.to_string().trim().is_empty() {
                    return legacy_action(window,&target.CurrentName().map_err(api_error)?.to_string(),&pattern);
                }
            }
            Err("PATTERN_UNAVAILABLE: no supported semantic action on this control; no coordinate fallback".into())
        }
        "scroll" => {
            let vertical=matches!(text,"up"|"down");
            let forward=matches!(text,"down"|"right");
            if !matches!(text,"up"|"down"|"left"|"right") { return Err("SCROLL_DIRECTION_INVALID".into()); }
            let mut class=[0u16;128];
            let count=windows::Win32::UI::WindowsAndMessaging::GetClassNameW(window,&mut class);
            if !name.is_empty()&&String::from_utf16_lossy(&class[..count.max(0) as usize]).starts_with("Chrome_WidgetWin_") {
                return scroll_once(uia,window,text,name);
            }
            let condition=uia.CreatePropertyCondition(UIA_IsScrollPatternAvailablePropertyId,&windows::core::VARIANT::from(true)).map_err(api_error)?;
            let found=root.FindAll(TreeScope_Subtree,&condition).map_err(api_error)?;
            let mut patterns=Vec::new();
            if !name.is_empty() {
                let mut current=element(&uia,&root,name,"scroll")?;
                let walker=uia.RawViewWalker().map_err(api_error)?;
                for _ in 0..32 {
                    if let Ok(p)=current.GetCurrentPattern(UIA_ScrollPatternId).and_then(|p|p.cast::<IUIAutomationScrollPattern>()) {
                        let supported=if vertical {p.CurrentVerticallyScrollable()} else {p.CurrentHorizontallyScrollable()}.map_err(api_error)?.as_bool();
                        if supported {patterns.push((current.clone(),p));break;}
                    }
                    if uia.CompareElements(&current,&root).map_err(api_error)?.as_bool(){break;}
                    current=walker.GetParentElement(&current).map_err(api_error)?;
                }
            }
            for i in 0..found.Length().map_err(api_error)?.min(256) {
                if !name.is_empty(){break;}
                let el=found.GetElement(i).map_err(api_error)?;
                if el.CurrentIsOffscreen().map_err(api_error)?.as_bool() {continue;}
                let p=el.GetCurrentPattern(UIA_ScrollPatternId).and_then(|p|p.cast::<IUIAutomationScrollPattern>()).map_err(api_error)?;
                let supported=if vertical {p.CurrentVerticallyScrollable()} else {p.CurrentHorizontallyScrollable()}.map_err(api_error)?.as_bool();
                if supported {patterns.push((el,p));}
            }
            if patterns.is_empty()&&!name.is_empty(){return native_scroll(window,&element(&uia,&root,name,"scroll")?,text);}
            if patterns.len()!=1 {return Err(format!("SCROLL_TARGET_NOT_UNIQUE: {} visible scroll containers; pass target with an observed container or child name",patterns.len()));}
            let (container,p)=&patterns[0];
            let container_id=crate::window_query::runtime_id(container)?;
            let before=fresh_scroll_percent(uia,window,&container_id,vertical,name)?;
            if (forward&&before>=100.0)||(!forward&&before<=0.0) {return Ok("scroll_boundary_reached".into());}
            // Chromium providers can acknowledge relative Scroll without moving.
            // Choose one absolute semantic operation BEFORE mutation, never retry
            // with injected input. One step is a quarter of the visible viewport.
            let view=if vertical {p.CurrentVerticalViewSize()} else {p.CurrentHorizontalViewSize()}.map_err(api_error)?;
            let delta=(25.0*view/(100.0-view).max(0.01)).clamp(0.01,100.0);
            let destination=(before+if forward{delta}else{-delta}).clamp(0.0,100.0);
            p.SetScrollPercent(if vertical{-1.0}else{destination},if vertical{destination}else{-1.0}).map_err(api_error)?;
            let deadline=std::time::Instant::now()+std::time::Duration::from_millis(2500);
            loop {
                let after=fresh_scroll_percent(uia,window,&container_id,vertical,name).map_err(|e|format!("SCROLL_READBACK_FAILED: {e}; do not repeat"))?;
                let change=after-before;
                if (forward&&change>0.001)||(!forward&&change< -0.001){break;}
                if std::time::Instant::now()>=deadline{return Err(format!("SCROLL_UNCONFIRMED: before={before:.6}, requested={destination:.6}, observed={after:.6}, view={view:.6}; do not repeat"));}
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            Ok(format!("scroll_verified: before={before:.6}, requested={destination:.6}"))
        }
        "key" => {
            let target_name = selection.target(key)?.to_string();
            match text.trim().to_lowercase().replace("control", "ctrl").as_str() {
                "ctrl+a" => { selection.replace_all = true; Ok("agent_selection_all".into()) }
                "backspace" | "delete" if selection.replace_all => execute_on(uia,window, "text", "", &target_name, selection),
                "enter" | "return" => {
                    let target = element(&uia,&root,&target_name,"key")?;
                    if let Ok(pattern) = target.GetCurrentPattern(UIA_InvokePatternId).and_then(|p| p.cast::<IUIAutomationInvokePattern>()) {
                        pattern.Invoke().map_err(api_error)?;
                        return Ok("default_action_acknowledged".into());
                    }
                    if let Ok(pattern) = target.GetCurrentPattern(UIA_LegacyIAccessiblePatternId).and_then(|p| p.cast::<IUIAutomationLegacyIAccessiblePattern>()) {
                        if !pattern.CurrentDefaultAction().map_err(api_error)?.to_string().trim().is_empty() {
                            return legacy_action(window,&target.CurrentName().map_err(api_error)?.to_string(),&pattern);
                        }
                    }
                    Err("KEY_UNSUPPORTED: selected element exposes no semantic default action; no physical Enter fallback".into())
                }
                "right" | "left" => {
                    let target = element(&uia,&root,&target_name,"key")?;
                    let pattern = target.GetCurrentPattern(UIA_ExpandCollapsePatternId).and_then(|p| p.cast::<IUIAutomationExpandCollapsePattern>()).map_err(api_error)?;
                    let expand = text.trim().eq_ignore_ascii_case("right");
                    let expected = if expand { ExpandCollapseState_Expanded } else { ExpandCollapseState_Collapsed };
                    if expand { pattern.Expand() } else { pattern.Collapse() }.map_err(api_error)?;
                    verify_observation(|| Ok(pattern.CurrentExpandCollapseState().map_err(api_error)? == expected), "EXPANSION_UNCONFIRMED", settle)?;
                    Ok("expansion_verified".into())
                }
                _ => Err("KEY_UNSUPPORTED: this shortcut has no semantic implementation yet; use a named control action".into()),
            }
        }
        _ => Err("ACTION_UNSUPPORTED: native semantic implementation not yet available".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn adjacent_scroll_item_respects_direction_and_visible_boundaries(){
        assert_eq!(adjacent_offscreen(&[false,false,true,true,false,false],false),Some(1));
        assert_eq!(adjacent_offscreen(&[false,false,true,true,false,false],true),Some(4));
        assert_eq!(adjacent_offscreen(&[true,true,false],false),None);
        assert_eq!(adjacent_offscreen(&[false,true,true],true),None);
        assert_eq!(adjacent_offscreen(&[false,false],true),None);
        assert_eq!(adjacent_offscreen(&[],false),None);
    }
    #[test]
    fn richedit_paragraph_readback_preserves_blank_lines_and_literal_slashes() {
        assert_eq!(provider_text("a\r\rb",UIA_DocumentControlTypeId),provider_text("a\r\n\r\nb",UIA_DocumentControlTypeId));
        assert_ne!(provider_text("a\rb",UIA_DocumentControlTypeId),provider_text("a\r\n\r\nb",UIA_DocumentControlTypeId));
        assert_eq!(provider_text(r"C:\new\text",UIA_DocumentControlTypeId),r"C:\new\text");
        assert_eq!(provider_text("a\rb",UIA_EditControlTypeId),"a\rb");
    }
    #[test]
    fn explorer_menu_label_is_not_a_second_action_target() {
        let observed = [UIA_MenuItemControlTypeId, UIA_TextControlTypeId];
        assert_eq!(observed.into_iter().filter(|kind| can_be_action_target(*kind,"click")).count(),1);
        let ambiguous = [UIA_MenuItemControlTypeId, UIA_MenuItemControlTypeId];
        assert_eq!(ambiguous.into_iter().filter(|kind| can_be_action_target(*kind,"click")).count(),2);
        let rename = [UIA_ListItemControlTypeId, UIA_EditControlTypeId];
        assert_eq!(rename.into_iter().filter(|kind| can_be_action_target(*kind,"text")).count(),1);
        let popup=[UIA_ButtonControlTypeId,UIA_WindowControlTypeId,UIA_TextControlTypeId];
        assert_eq!(popup.into_iter().filter(|kind|can_be_action_target(*kind,"click")).count(),1);
    }
    #[test]
    fn value_pattern_does_not_turn_checkboxes_or_buttons_into_text_fields() {
        assert!(is_text_control(UIA_EditControlTypeId));
        assert!(is_text_control(UIA_DocumentControlTypeId));
        assert!(!is_text_control(UIA_CheckBoxControlTypeId));
        assert!(!is_text_control(UIA_ButtonControlTypeId));
        assert!(!is_text_control(UIA_SliderControlTypeId));
    }
    #[test]
    fn asynchronous_readback_waits_without_repeating_the_write() {
        let mut observations = ["old", "old", "new"].into_iter();
        let mut waits = 0;
        verify_value(|| Ok(observations.next().unwrap().into()), "new", || waits += 1).unwrap();
        assert_eq!(waits, 2);
    }
    #[test]
    fn unverified_value_and_provider_failure_remain_errors() {
        let mut reads = 0;
        assert!(verify_value(|| { reads += 1; Ok("old".into()) }, "new", || {}).is_err());
        assert_eq!(reads, 26);
        assert_eq!(verify_value(|| Err("provider gone".into()), "new", || {}).unwrap_err(), "provider gone");
    }
    #[test]
    fn agent_target_is_window_bound_not_windows_focus() {
        let mut s = AgentSelection::default();
        assert!(s.target(1).is_err());
        s.select(1, "Message");
        assert_eq!(s.target(1).unwrap(), "Message");
        assert!(s.target(2).is_err());
    }
    #[test]
    fn selection_and_unicode_are_owned_by_agent() {
        let mut s = AgentSelection::default();
        s.select(1, "Message");
        assert_eq!(s.insert("a", "🦉\n"), "a🦉\n");
        s.replace_all = true;
        assert_eq!(s.insert("old", "new"), "new");
        s.select(1, "Other");
        assert!(!s.replace_all);
    }
}
