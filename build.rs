fn main() {
    let mut res = winresource::WindowsResource::new();
    res.set_icon("directshell.ico");
    res.compile().unwrap();
}
