import asyncio
from playwright.async_api import async_playwright
from icalendar import Calendar, Event
from datetime import datetime, timedelta
import pytz
import os
import re

CAMBRIDGE_URL = "https://student.szkolafilmowa.pl/palio/html.run?_Instance=cambridge"
AZURE_EMAIL = os.environ.get("AZURE_EMAIL")
AZURE_PASSWORD = os.environ.get("AZURE_PASSWORD")
OUTPUT_FILE = "plan_zajec.ics"

async def login_and_get_schedule():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        print("🌐 Otwieram stronę Cambridge...")
        await page.goto(CAMBRIDGE_URL)
        await page.wait_for_timeout(3000)
        
        print("🔐 Klikam 'Zaloguj przez Azure'...")
        await page.click('input[value="Zaloguj przez Azure"]')
        await page.wait_for_timeout(3000)
        
        print("📧 Wprowadzam email...")
        await page.wait_for_selector('input[type="email"]', timeout=15000)
        await page.fill('input[type="email"]', AZURE_EMAIL)
        await page.click('input[type="submit"]')
        await page.wait_for_timeout(3000)
        
        print("🔑 Wprowadzam hasło...")
        await page.wait_for_selector('input[type="password"]', timeout=15000)
        await page.fill('input[type="password"]', AZURE_PASSWORD)
        await page.click('input[type="submit"]')
        await page.wait_for_timeout(5000)
        
        for selector in ['input[value="No"]', 'input[value="Nie"]', '#idBtn_Back']:
            if await page.locator(selector).count() > 0:
                await page.click(selector)
                await page.wait_for_timeout(3000)
                break
        
        await page.wait_for_timeout(5000)
        await page.screenshot(path="debug_01_logged_in.png")
        
        print("📅 Przechodzę do harmonogramu...")
        harmonogram_url = await page.evaluate('''
            () => {
                const html = document.documentElement.innerHTML;
                const match = html.match(/(\\/palio\\/html\\.run\\?[^"']*_PageID=191[^"']*)/);
                return match ? match[1].replace(/&amp;/g, '&') : null;
            }
        ''')
        
        if harmonogram_url:
            await page.goto(f"https://student.szkolafilmowa.pl{harmonogram_url}")
            await page.wait_for_timeout(3000)
        
        print("📋 Klikam na album...")
        album_link = await page.evaluate('''
            () => {
                const link = document.querySelector('table.sort tbody a.link');
                return link ? link.getAttribute('href') : null;
            }
        ''')
        
        if album_link:
            await page.goto(f"https://student.szkolafilmowa.pl{album_link}")
            await page.wait_for_timeout(5000)
        
        await page.screenshot(path="debug_02_schedule.png")
        
        print("📊 Pobieram zajęcia z 12 tygodni...")
        
        all_events = []
        
        events_week1 = await extract_events_from_page(page)
        all_events.extend(events_week1)
        print(f"  Tydzień 1: {len(events_week1)} zajęć")
        
        # ZMIANA: 12 tygodni zamiast 4
        for week_num in range(2, 13):
            try:
                next_button = page.locator('a[href="javascript:goForward();"]')
                if await next_button.count() > 0:
                    await next_button.click()
                    await page.wait_for_timeout(2000)
                    
                    events_week = await extract_events_from_page(page)
                    all_events.extend(events_week)
                    print(f"  Tydzień {week_num}: {len(events_week)} zajęć")
            except Exception as e:
                print(f"  ⚠️ Tydzień {week_num}: {e}")
                break
        
        await page.screenshot(path="debug_03_final.png")
        
        html_content = await page.content()
        with open("debug_harmonogram_page.html", "w", encoding="utf-8") as f:
            f.write(html_content)
        
        await browser.close()
        
        print(f"📋 Łącznie znaleziono {len(all_events)} zajęć")
        return all_events

async def extract_events_from_page(page):
    events = await page.evaluate('''
        () => {
            const events = [];
            
            const leftToDay = {
                20: 0, 150: 1, 280: 2, 410: 3, 540: 4, 670: 5, 800: 6
            };
            
            const dates = {};
            document.querySelectorAll('div[style*="position:absolute"]').forEach(div => {
                const style = div.getAttribute('style') || '';
                const leftMatch = style.match(/left:\\s*(\\d+)/);
                const topMatch = style.match(/top:\\s*-40/);
                
                if (leftMatch && topMatch) {
                    const left = parseInt(leftMatch[1]);
                    const dateMatch = div.innerText.match(/(\\d{2})-(\\d{2})-(\\d{4})/);
                    if (dateMatch) {
                        const dayIndex = leftToDay[left];
                        if (dayIndex !== undefined) {
                            dates[left] = `${dateMatch[3]}-${dateMatch[2]}-${dateMatch[1]}`;
                        }
                    }
                }
            });
            
            document.querySelectorAll('div[onmouseover]').forEach(div => {
                const onmouseover = div.getAttribute('onmouseover') || '';
                const style = div.getAttribute('style') || '';
                
                const leftMatch = style.match(/left:\\s*(\\d+)/);
                if (!leftMatch) return;
                
                const left = parseInt(leftMatch[1]);
                
                let closestLeft = 20;
                let minDiff = Math.abs(left - 20);
                for (const l of [20, 150, 280, 410, 540, 670, 800]) {
                    const diff = Math.abs(left - l);
                    if (diff < minDiff) {
                        minDiff = diff;
                        closestLeft = l;
                    }
                }
                
                const date = dates[closestLeft];
                if (!date) return;
                
                const tooltipMatch = onmouseover.match(/showtip\\(['"](.*?)['"]\\)/s);
                if (!tooltipMatch) return;
                
                let tooltip = tooltipMatch[1];
                tooltip = tooltip.replace(/&quot;/g, '"')
                                 .replace(/&lt;/g, '<')
                                 .replace(/&gt;/g, '>')
                                 .replace(/&amp;/g, '&')
                                 .replace(/<[^>]*>/g, '\\n')
                                 .replace(/\\\\n/g, '\\n');
                
                const lines = tooltip.split('\\n').map(l => l.trim()).filter(l => l);
                
                if (lines.length >= 3) {
                    const title = lines[0];
                    const lecturer = lines[1] || '';
                    
                    let timeStart = '', timeEnd = '';
                    for (const line of lines) {
                        const timeMatch = line.match(/(\\d{1,2}:\\d{2})-(\\d{1,2}:\\d{2})/);
                        if (timeMatch) {
                            timeStart = timeMatch[1];
                            timeEnd = timeMatch[2];
                            break;
                        }
                    }
                    
                    let room = '';
                    for (const line of lines) {
                        if (line.startsWith('Sala:')) {
                            room = line.replace('Sala:', '').trim();
                            break;
                        }
                    }
                    
                    if (title && timeStart && timeEnd) {
                        events.push({
                            title: title,
                            lecturer: lecturer,
                            date: date,
                            time_start: timeStart,
                            time_end: timeEnd,
                            room: room
                        });
                    }
                }
            });
            
            return events;
        }
    ''')
    
    return events

def create_ics(events):
    cal = Calendar()
    cal.add('prodid', '-//Plan Zajec Szkola Filmowa//PL')
    cal.add('version', '2.0')
    cal.add('calscale', 'GREGORIAN')
    cal.add('method', 'PUBLISH')
    cal.add('x-wr-calname', 'Plan Zajęć - Szkoła Filmowa')
    cal.add('x-wr-timezone', 'Europe/Warsaw')
    
    tz = pytz.timezone('Europe/Warsaw')
    added = 0
    
    for event_data in events:
        try:
            event = Event()
            
            title = event_data['title']
            if event_data.get('lecturer'):
                title = f"{title} ({event_data['lecturer']})"
            
            event.add('summary', title)
            
            date_str = event_data['date']
            start_time = event_data['time_start']
            end_time = event_data['time_end']
            
            if len(start_time) == 4:
                start_time = '0' + start_time
            if len(end_time) == 4:
                end_time = '0' + end_time
            
            start_dt = datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(f"{date_str} {end_time}", "%Y-%m-%d %H:%M")
            
            event.add('dtstart', tz.localize(start_dt))
            event.add('dtend', tz.localize(end_dt))
            
            if event_data.get('room'):
                event.add('location', event_data['room'])
            
            description = f"Prowadzący: {event_data.get('lecturer', 'N/A')}"
            event.add('description', description)
            
            uid = f"{start_dt.strftime('%Y%m%d%H%M')}-{abs(hash(title)) % 100000}@szkolafilmowa"
            event.add('uid', uid)
            
            cal.add_component(event)
            added += 1
            
            print(f"  ✅ {date_str} {start_time}-{end_time} {event_data['title'][:40]}")
            
        except Exception as e:
            print(f"  ⚠️ Błąd: {e}")
            continue
    
    with open(OUTPUT_FILE, 'wb') as f:
        f.write(cal.to_ical())
    
    print(f"✅ Zapisano {added} wydarzeń do {OUTPUT_FILE}")
    return added

async def main():
    print("🚀 Start synchronizacji (12 tygodni)...")
    print(f"📧 Email: {AZURE_EMAIL}")
    print(f"🔑 Hasło: {'*' * 8 if AZURE_PASSWORD else 'BRAK!'}")
    
    if not AZURE_EMAIL or not AZURE_PASSWORD:
        print("❌ Brak AZURE_EMAIL lub AZURE_PASSWORD!")
        cal = Calendar()
        cal.add('prodid', '-//Plan Zajec//PL')
        cal.add('version', '2.0')
        cal.add('x-wr-calname', 'BŁĄD - brak konfiguracji')
        with open(OUTPUT_FILE, 'wb') as f:
            f.write(cal.to_ical())
        return
    
    events = await login_and_get_schedule()
    
    print(f"📊 Pobrano {len(events)} zajęć")
    
    if events:
        create_ics(events)
    else:
        print("⚠️ Brak zajęć - tworzę pusty kalendarz")
        cal = Calendar()
        cal.add('prodid', '-//Plan Zajec//PL')
        cal.add('version', '2.0')
        cal.add('x-wr-calname', 'Plan Zajęć - Szkoła Filmowa')
        
        event = Event()
        event.add('summary', '⚠️ Brak zajęć w tym okresie')
        tz = pytz.timezone('Europe/Warsaw')
        now = datetime.now(tz)
        event.add('dtstart', now)
        event.add('dtend', now + timedelta(hours=1))
        event.add('uid', f'info-{now.strftime("%Y%m%d%H%M")}@szkolafilmowa')
        cal.add_component(event)
        
        with open(OUTPUT_FILE, 'wb') as f:
            f.write(cal.to_ical())

if __name__ == "__main__":
    asyncio.run(main())
