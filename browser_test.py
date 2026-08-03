from playwright.sync_api import sync_playwright

def find_in_page(page, name):
    """Browser locator: try to find an element by name across roles."""
    for role in ["button", "link", "textbox", "checkbox"]:
        try:
            el = page.get_by_role(role, name=name)
            if el.count() > 0:
                print(f"   FOUND '{name}' as {role}")
                return el.first
        except Exception:
            continue
    # fallback: find by visible text
    try:
        el = page.get_by_text(name, exact=False)
        if el.count() > 0:
            print(f"   FOUND '{name}' by text")
            return el.first
    except Exception:
        pass
    print(f"   NOT FOUND: '{name}'")
    return None

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]

    # find the tab that has the page we want (not just pages[0])
    page = context.pages[0]
    for pg in context.pages:
        if "Google" in pg.title():
            page = pg
            break

    print("Using page:", page.title())

    # test finding a specific element by name (like a real plan step)
    el = find_in_page(page, "Gmail")      # Gmail is a link on Google
    if el:
        print("   (found it - would click/act here)")

    browser.close()