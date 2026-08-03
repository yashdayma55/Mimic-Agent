from pywinauto import Desktop

d = Desktop(backend="uia")
for win in d.windows():
    if "Notepad" not in win.window_text():
        continue
    print("Notepad window:", win.window_text())
    # find ANY document/edit element, whatever it's named
    for el in win.descendants():
        t = el.element_info.control_type
        if t in ("Document", "Edit"):
            print(f"   found [{t}] named '{el.element_info.name}'")
            el.click_input()
            print("   clicked into it!")
            break
    break