from pywinauto import Desktop

def find_and_click(elem_name, elem_type, do_click=False):
    """Tier 1 locator: find by name + type, optionally click."""
    desktop = Desktop(backend="uia")
    print(f"Looking for {elem_type} named '{elem_name}'...")
    for win in desktop.windows():
        try:
            matches = win.descendants(title=elem_name, control_type=elem_type)
            if matches:
                el = matches[0]
                rect = el.rectangle()
                print(f"FOUND in '{win.window_text()[:30]}' at {rect}")
                if do_click:
                    el.click_input()
                    print(">>> CLICKED it!")
                else:
                    print(f"Would click ({rect.mid_point().x}, {rect.mid_point().y}) - set do_click=True to click")
                return el
        except Exception:
            continue
    print("NOT FOUND")
    return None

# TEST: start with do_click=False to confirm it finds it,
# then flip to True to actually click.
find_and_click("Minimize", "Button", do_click=True)