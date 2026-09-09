//! Optional ATTIA sidecar lifecycle. Standalone DS keeps its existing behavior.
use std::sync::atomic::{AtomicBool, Ordering};
use windows::core::{Error, Result};
use windows::Win32::Foundation::{CloseHandle, HWND};
use windows::Win32::System::Threading::{OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE};
use windows::Win32::UI::WindowsAndMessaging::{GetWindowThreadProcessId, IsWindow};

static ENABLED: AtomicBool = AtomicBool::new(false);
pub fn enabled() -> bool { ENABLED.load(Ordering::SeqCst) }

pub fn configure() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() == 1 { return Ok(()); }
    if args.len() != 3 || args[1] != "--attia-owner" { return Err(Error::from_win32()); }
    let owner: u32 = args[2].parse().map_err(|_| Error::from_win32())?;
    let handle = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, false, owner)? };
    let raw_handle = handle.0 as isize;
    ENABLED.store(true, Ordering::SeqCst);
    std::thread::spawn(move || unsafe {
        let handle = windows::Win32::Foundation::HANDLE(raw_handle as *mut _);
        WaitForSingleObject(handle, u32::MAX);
        let _ = CloseHandle(handle);
        std::process::exit(0);
    });
    Ok(())
}

/// Never let the helper operate the ATTIA approval surface itself.
pub unsafe fn allowed_target(target: HWND) -> bool {
    if target.0.is_null() || !IsWindow(target).as_bool() { return false; }
    let mut pid = 0;
    GetWindowThreadProcessId(target, Some(&mut pid));
    let Ok(handle) = OpenProcess(super::PROCESS_QUERY_LIMITED_INFORMATION, false, pid) else { return false; };
    let mut buf = vec![0u16; 32768];
    let mut size = buf.len() as u32;
    let ok = super::QueryFullProcessImageNameW(handle, super::PROCESS_NAME_FORMAT(0), windows::core::PWSTR(buf.as_mut_ptr()), &mut size).is_ok();
    let _ = CloseHandle(handle);
    if !ok { return false; }
    let exe = String::from_utf16_lossy(&buf[..size as usize]).to_lowercase();
    !["\\attia.exe", "\\consent.exe", "\\logonui.exe", "\\credentialuibroker.exe", "\\directshell.exe"].iter().any(|name| exe.ends_with(name))
}
