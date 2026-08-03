from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]

    page = context.pages[0]
    for pg in context.pages:
        if "Google" in pg.title():
            page = pg
            break
    page.bring_to_front()
    print("Replaying on page:", page.title())

    # find the actual search TEXT INPUT and fill it directly
    print("\n[STEP] Fill the search box")
    filled = False
    for role in ["combobox", "textbox", "searchbox"]:
        try:
            box = page.get_by_role(role).first
            if box.count() > 0:
                box.click()
                box.fill("mimicagent replay test")
                print(f"      FILLED into the {role}")
                filled = True
                break
        except Exception as e:
            print(f"      {role} failed: {e}")
    if not filled:
        print("      no text input found")

    time.sleep(3)
    print("\n=== done - check the search box ===")
    browser.close()