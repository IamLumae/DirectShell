//! One owner for UIA client policy. COM must be initialized by the calling thread.
use windows::core::{Error, Interface, Result};
use windows::Win32::Foundation::E_FAIL;
use windows::Win32::System::Com::{CoCreateInstance, CLSCTX_INPROC_SERVER};
use windows::Win32::UI::Accessibility::{CUIAutomation8, IUIAutomation, IUIAutomation2};

pub unsafe fn create() -> Result<IUIAutomation> {
    let client: IUIAutomation = CoCreateInstance(&CUIAutomation8, None, CLSCTX_INPROC_SERVER)?;
    let policy = client.cast::<IUIAutomation2>()?;
    // UIA itself otherwise focuses before Invoke/SetValue, even when our code
    // contains no focus setter. Configure BEFORE obtaining elements/patterns.
    policy.SetAutoSetFocus(false)?;
    if policy.AutoSetFocus()?.as_bool() {
        return Err(Error::new(E_FAIL, "UIA automatic focus could not be disabled"));
    }
    Ok(client)
}

#[cfg(test)]
mod tests {
    use super::*;
    use windows::core::Interface;
    use windows::Win32::System::Com::{CoInitializeEx, CoUninitialize, COINIT_MULTITHREADED};
    use windows::Win32::UI::Accessibility::IUIAutomation2;

    #[test]
    fn every_new_client_disables_automatic_focus_before_any_target_lookup() {
        // Real COM configuration readback; no window lookup or UI action.
        std::thread::spawn(|| unsafe {
            CoInitializeEx(None, COINIT_MULTITHREADED).ok().unwrap();
            struct Apartment;
            impl Drop for Apartment { fn drop(&mut self) { unsafe { CoUninitialize(); } } }
            let _apartment = Apartment;
            for _ in 0..2 {
                let client = create().unwrap();
                assert!(!client.cast::<IUIAutomation2>().unwrap().AutoSetFocus().unwrap().as_bool(),
                    "UIA would automatically focus targets even without explicit SetFocus calls");
            }
        }).join().unwrap();
    }
}
