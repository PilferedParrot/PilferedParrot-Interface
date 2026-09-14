"""Measure rendered text contrast against bright artwork through Minimal surfaces."""
import base64
import os
import unittest

try:
    from playwright.sync_api import sync_playwright, expect
except ModuleNotFoundError:
    if os.environ.get('PILFEREDPARROT_REQUIRE_PLAYWRIGHT') == '1':
        raise
    sync_playwright = None

from playwright_fixture import PilferedParrotBrowserFixture
import test_theme_balance_browser_e2e as theme_fixtures


@unittest.skipUnless(sync_playwright, 'install requirements-browser.txt')
class ArtworkContrastTests(unittest.TestCase):
    def test_brightest_artwork_retains_contrast_in_work_and_chat(self):
        fixture = PilferedParrotBrowserFixture()
        self.addCleanup(fixture.stop)
        theme = theme_fixtures.ThemeBalanceBrowserEndToEndTests._theme('bright-artwork')
        theme.update(background=True, background_url='/api/browser/theme/background',
                     background_repeat='repeat', background_alignment='center')
        fixture.app.browser_theme = lambda: theme
        playwright = sync_playwright().start()
        self.addCleanup(playwright.stop)
        browser = playwright.chromium.launch(headless=True)
        self.addCleanup(browser.close)
        context = browser.new_context(viewport={'width': 1200, 'height': 800},
                                      device_scale_factor=1)
        context.route('**/api/browser/theme/background*', lambda route: route.fulfill(
            status=200, content_type='image/png', body=theme_fixtures.ThemeBalanceBrowserEndToEndTests._png_bytes()))
        for kind in ('work', 'chat'):
            with self.subTest(kind=kind):
                page = context.new_page()
                self.addCleanup(page.close)
                if kind == 'work':
                    url = fixture.browser_url
                else:
                    capability = fixture.app.issue_capability('chat', provider='codex')
                    url = f'{fixture.base_url}/chat#capability={capability}&provider=codex'
                page.goto(url)
                expect(page.locator('#prompt' if kind == 'work' else '#chatPrompt')).to_be_enabled()
                page.wait_for_function("() => getComputedStyle(document.body).getPropertyValue('--artwork-veil').trim() !== 'transparent'")
                page.evaluate("""() => {
                    const sample = document.createElement('article');
                    sample.id = 'contrastSample';
                    sample.className = 'message';
                    sample.style.cssText = 'position:fixed;left:400px;top:250px;width:300px;height:120px;z-index:100';
                    sample.textContent = 'Text remains clear over stars';
                    document.body.append(sample);
                }""")
                screenshot = base64.b64encode(page.screenshot()).decode('ascii')
                contrast = page.evaluate("""async encoded => {
                    const image = new Image();
                    const bytes = Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
                    image.src = URL.createObjectURL(new Blob([bytes], {type:'image/png'}));
                    await image.decode();
                    const canvas = document.createElement('canvas');
                    canvas.width = image.width; canvas.height = image.height;
                    const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
                    URL.revokeObjectURL(image.src);
                    const backdrop = [...context.getImageData(430, 330, 1, 1).data].slice(0, 3);
                    context.fillStyle = getComputedStyle(document.querySelector('#contrastSample')).color;
                    context.fillRect(0, 0, 1, 1);
                    const foreground = [...context.getImageData(0, 0, 1, 1).data].slice(0, 3);
                    const luminance = color => color.map(c => c / 255)
                        .map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4)
                        .reduce((sum, c, i) => sum + c * [.2126, .7152, .0722][i], 0);
                    const a = luminance(foreground), b = luminance(backdrop);
                    return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
                }""", screenshot)
                self.assertGreaterEqual(contrast, 4.5, kind)
                page.close()
