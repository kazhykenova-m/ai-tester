from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)  # False: вы увидите окно браузера
    page = browser.new_page()
    page.goto("https://www.saucedemo.com")
    page.fill("#user-name", "standard_user")
    page.fill("#password", "secret_sauce")
    page.click("#login-button")
    page.wait_for_url("**/inventory.html")
    print("Вход выполнен, заголовок:", page.title())
    page.screenshot(path="smoke.png")
    browser.close()