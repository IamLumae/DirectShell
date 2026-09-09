//! Short-lived base-window API, sharing the modern, no-auto-focus UIA client.
use std::io::{Read,Write};
use serde_json::{json,Value};
use windows::Win32::{Foundation::*,System::{Com::*,Ole::*,Threading::*},UI::{Accessibility::*,WindowsAndMessaging::*}};
use windows::core::Interface;

pub unsafe fn runtime_id(element:&IUIAutomationElement)->Result<String,String>{
    let array=element.GetRuntimeId().map_err(|e|e.to_string())?;
    struct Array(*mut SAFEARRAY);impl Drop for Array{fn drop(&mut self){unsafe{let _=SafeArrayDestroy(self.0);}}}
    let _owner=Array(array);
    let low=SafeArrayGetLBound(array,1).map_err(|e|e.to_string())?;
    let high=SafeArrayGetUBound(array,1).map_err(|e|e.to_string())?;
    if high-low>128{return Err("Runtime ID exceeds limit".into());}
    let mut values=Vec::new();for i in low..=high{let mut n=0i32;SafeArrayGetElement(array,&i,&mut n as *mut _ as *mut _).map_err(|e|e.to_string())?;values.push(n.to_string());}
    Ok(values.join(","))
}
unsafe fn window(reference:&str)->Result<HWND,String>{
    let p=reference.split(':').map(|s|s.parse::<u64>()).collect::<Result<Vec<_>,_>>().map_err(|_|"Use an exact reference from desktop_windows")?;
    if p.len()!=3{return Err("Use an exact reference from desktop_windows".into());}
    let hwnd=HWND(p[2] as *mut _);let mut pid=0;GetWindowThreadProcessId(hwnd,Some(&mut pid));
    if pid as u64!=p[0]||!IsWindowVisible(hwnd).as_bool()||!crate::attia::allowed_target(hwnd){return Err("Window changed or protected".into());}
    if ["cmd.exe","powershell.exe","pwsh.exe","windowsterminal.exe","openconsole.exe","conhost.exe","wscript.exe","cscript.exe","mshta.exe","regedit.exe","mmc.exe"].contains(&crate::get_exe_name(pid).to_lowercase().as_str()){return Err("This window cannot be automated by the base window tools".into());}
    let process=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,false,pid).map_err(|e|e.to_string())?;
    let (mut created,mut exit,mut kernel,mut user)=(FILETIME::default(),FILETIME::default(),FILETIME::default(),FILETIME::default());
    let result=GetProcessTimes(process,&mut created,&mut exit,&mut kernel,&mut user);let _=CloseHandle(process);result.map_err(|e|e.to_string())?;
    let ticks=((created.dwHighDateTime as u64)<<32)+created.dwLowDateTime as u64+504911232000000000;
    if ticks!=p[1]{return Err("Window process was replaced; read inventory again".into());}
    Ok(hwnd)
}
unsafe fn query(request:Value)->Result<Value,String>{
    if request["tool"]!="desktop_window_read"&&request["tool"]!="desktop_window_action"&&request["tool"]!="_ds_native_scroll"{return Err("Unknown native window operation".into());}
    let a=&request["arguments"];
    let hwnd=window(a["window"].as_str().ok_or("Window reference required")?)?;
    let uia=crate::automation_client::create().map_err(|e|e.to_string())?;
    let handler:IUIAutomationFocusChangedEventHandler=crate::UiaFocusHandler.into();
    uia.AddFocusChangedEventHandler(None,&handler).map_err(|e|e.to_string())?;
    if request["tool"]=="_ds_native_scroll"{
        let outcome=crate::native_actions::scroll_once(&uia,hwnd,a["direction"].as_str().ok_or("Direction required")?,a["target"].as_str().ok_or("Target required")?)?;
        return Ok(json!({"outcome":outcome}));
    }
    let root=uia.ElementFromHandle(hwnd).map_err(|e|e.to_string())?;
    let walker=uia.RawViewWalker().map_err(|e|e.to_string())?;
    let action=request["tool"]=="desktop_window_action";
    let mut queue=std::collections::VecDeque::from([(root,0)]);let mut rows=Vec::new();let mut visited=0;let mut budget=24000usize;
    while let Some((el,depth))=queue.pop_front(){
        visited+=1;if visited>4000{break;}
        if el.CurrentIsPassword().map(|v|v.as_bool()).unwrap_or(true){continue;}
        let name=el.CurrentName().map(|v|v.to_string()).unwrap_or_default();
        let kind=el.CurrentControlType().map_err(|e|e.to_string())?;
        let id=runtime_id(&el)?;
        let enabled=el.CurrentIsEnabled().map(|v|v.as_bool()).unwrap_or(false);
        let control=crate::role_name(kind.0).to_string();
        if action && Some(id.as_str())==a["runtime_id"].as_str(){
            if Some(name.as_str())!=a["name"].as_str()||Some(control.as_str())!=a["control"].as_str()||!enabled{return Err("Element changed; read again".into());}
            let operation=match a["action"].as_str(){Some("focus")=>return Ok(json!({"action":"focus","completed":true,"meaning":"agent_selection_only","windows_focus_changed":false})),Some("invoke")=>"click",Some("set_value")=>"text",_=>return Err("Unsupported action".into())};
            let mut selection=crate::native_actions::AgentSelection::default();
            let target=format!("@runtime[{id}]:{name}");
            let outcome=crate::native_actions::execute(hwnd,operation,a["text"].as_str().unwrap_or(""),&target,&mut selection)?;
            return Ok(json!({"action":a["action"],"completed":true,"outcome":outcome,"windows_focus_changed":false}));
        }
        if !action&&!el.CurrentIsOffscreen().map(|v|v.as_bool()).unwrap_or(true)&&a["name"].as_str().map(|filter|filter==name).unwrap_or(true){
            let value=el.GetCurrentPattern(UIA_ValuePatternId).and_then(|p|p.cast::<IUIAutomationValuePattern>()).and_then(|p|p.CurrentValue()).map(|v|v.to_string()).unwrap_or_default();
            let clipped_name:String=name.chars().take(300).collect();let clipped_value:String=value.chars().take(1000).collect();
            budget=budget.saturating_sub(clipped_name.len()+clipped_value.len());
            let scroll=el.GetCurrentPattern(UIA_ScrollPatternId).and_then(|p|p.cast::<IUIAutomationScrollPattern>()).ok().map(|p|json!({"vertical":p.CurrentVerticallyScrollable().map(|v|v.as_bool()).unwrap_or(false),"vertical_percent":p.CurrentVerticalScrollPercent().ok(),"horizontal_percent":p.CurrentHorizontalScrollPercent().ok()}));
            let mut ancestor_scroll=Vec::new();
            if a["name"].is_string(){let mut parent=walker.GetParentElement(&el);for _ in 0..32{let Ok(node)=parent else{break;};if let Ok(p)=node.GetCurrentPattern(UIA_ScrollPatternId).and_then(|p|p.cast::<IUIAutomationScrollPattern>()){ancestor_scroll.push(json!({"name":node.CurrentName().map(|v|v.to_string()).unwrap_or_default(),"vertical":p.CurrentVerticallyScrollable().map(|v|v.as_bool()).unwrap_or(false),"vertical_percent":p.CurrentVerticalScrollPercent().ok()}));}parent=walker.GetParentElement(&node);}}
            rows.push(json!({"runtime_id":id,"name":clipped_name,"control":control,"enabled":enabled,"value":clipped_value,"depth":depth,"scroll":scroll,"ancestor_scroll":ancestor_scroll}));
            if rows.len()>=120||budget<1400{return Ok(json!({"controls":rows,"truncated":true}));}
        }
        if depth<32{let mut child=walker.GetFirstChildElement(&el);let mut count=0;while let Ok(node)=child{child=walker.GetNextSiblingElement(&node);queue.push_back((node,depth+1));count+=1;if count>=256{break;}}}
    }
    if action{Err("Element missing; read window again".into())}else{Ok(json!({"controls":rows,"truncated":visited>4000}))}
}

pub fn dispatch()->bool{
    let args:Vec<String>=std::env::args().collect();if args.get(1).map(String::as_str)!=Some("--attia-query"){return false;}
    let result=(||unsafe{
        if args.len()!=3||args[2].parse::<u32>().is_err(){return Err("Owner required".to_string());}
        let mut input=String::new();std::io::stdin().take(65537).read_to_string(&mut input).map_err(|e|e.to_string())?;
        if input.len()>65536{return Err("Request exceeds limit".into());}
        let request=serde_json::from_str(&input).map_err(|e|e.to_string())?;
        CoInitializeEx(None,COINIT_MULTITHREADED).ok().map_err(|e|e.to_string())?;
        let result=query(request);CoUninitialize();result
    })();
    let out=match result{Ok(data)=>json!({"ok":true,"data":data}),Err(error)=>json!({"ok":false,"error":error})};
    let _=writeln!(std::io::stdout(),"{out}");true
}
