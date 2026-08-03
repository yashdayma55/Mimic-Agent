from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]

    # use the first tab, navigate it to Google
    page = context.pages[0]
    page.goto("https://www.google.com")
    print("Navigated to Google")

    # find the search box by its role and interact with it
    search = page.get_by_role("combobox")      # Google's search box
    search.click()
    search.fill("mimicagent test")
    print("Typed into the search box via Playwright!")

    # read something back to prove we can see page content
    print("Page title:", page.title())

    # DON'T press enter - just prove we found and filled it
    browser.close()