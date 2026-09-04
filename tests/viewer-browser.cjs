// Real Chromium/noVNC/WayVNC/GTK acceptance; no mocked network.
const { chromium } = require(process.env.REALMS_PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const errors = [];
    page.on('pageerror', error => { errors.push(error.message); console.error('browser-error', error.message); });
    page.on('console', message => { if(message.type()==='error') console.error('browser-console', message.text()); });
    await page.goto(process.env.REALMS_VIEWER_URL);
    await page.waitForSelector('body[data-connected="true"]', { timeout: 15000 });
    assert.equal(await page.evaluate(() => location.hash), '');
    assert.match(await page.locator('#state').innerText(), /view only/);
    const fixture = () => JSON.parse(fs.readFileSync(process.env.REALMS_TEST_FIXTURE, 'utf8'));
    const before = fixture();
    const clickEntry = async () => {
      const box = await page.locator('canvas').boundingBox();
      await page.mouse.click(box.x + box.width * (960 / 1920), box.y + box.height * (456 / 1080));
    };
    await clickEntry();
    await page.keyboard.type('SHOULD_NOT_ARRIVE');
    await page.waitForTimeout(800);
    assert.equal(fixture().text, before.text, 'view only must suppress browser input');
    await page.getByRole('button', { name: 'Take over', exact: true }).click();
    await page.waitForSelector('body[data-control="true"][data-connected="true"]', { timeout: 15000 });
    await clickEntry();
    await page.keyboard.press('End');
    await page.keyboard.down('Shift');
    await page.keyboard.press('KeyB');
    await page.keyboard.up('Shift');
    await page.keyboard.type('rowser-ok');
    await page.waitForTimeout(800);
    assert.equal(fixture().text, before.text + 'Browser-ok', 'real GTK must receive takeover input');
    await page.screenshot({ path: process.env.REALMS_TEST_SCREENSHOT });
    await page.getByRole('button', { name: 'Return to agent', exact: true }).click();
    await page.waitForSelector('body[data-control="false"][data-connected="true"]', { timeout: 15000 });
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ connected: true, viewOnly: true, takeoverInput: true, returnedToAgent: true, errors }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
