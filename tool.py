import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from dotenv import load_dotenv
import re
import os
from langgraph.types import interrupt
from langchain_openai import ChatOpenAI
from CONFIG import GROQ_MODEL, OPENAI_MODEL

# Load API Keys
load_dotenv()

# Config
MATCH_THRESHOLD = 50
MAX_PAGES_TO_SCRAPE = 5  # Limit pagination to avoid infinite loops

# ------------------- Helper Functions -------------------

def _read_my_resume():
    try:
        with open("resume.txt", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None

def _extract_score(text):
    match = re.search(r"SCORE:\s*(\d+)%", text)
    if not match:
        match = re.search(r"(\d+)%", text)
    if match:
        return int(match.group(1))
    return 0

def _get_smart_domain(country_input: str):
    """
    Intelligently maps any country variation to the correct Indeed domain.
    """
    c = country_input.lower().strip()
    
    mapping = {
        # North America
        "usa": "indeed.com", "us": "indeed.com", "united states": "indeed.com", "america": "indeed.com",
        "canada": "ca.indeed.com", "ca": "ca.indeed.com",
        "mexico": "indeed.com.mx", "mx": "indeed.com.mx",
        
        # Europe
        "uk": "indeed.co.uk", "united kingdom": "indeed.co.uk", "britain": "indeed.co.uk",
        "germany": "de.indeed.com", "de": "de.indeed.com", "deutschland": "de.indeed.com",
        "france": "indeed.fr", "fr": "indeed.fr",
        "italy": "it.indeed.com", "it": "it.indeed.com",
        "spain": "indeed.es", "es": "indeed.es",
        "netherlands": "indeed.nl", "nl": "indeed.nl", "holland": "indeed.nl",
        
        # Asia / Middle East
        "uae": "ae.indeed.com", "united arab emirates": "ae.indeed.com", "dubai": "ae.indeed.com",
        "india": "in.indeed.com", "in": "in.indeed.com",
        "pakistan": "pk.indeed.com", "pk": "pk.indeed.com",
        "japan": "jp.indeed.com", "jp": "jp.indeed.com",
        "singapore": "indeed.com.sg", "sg": "indeed.com.sg",
        
        # Oceania
        "australia": "au.indeed.com", "au": "au.indeed.com",
        "new zealand": "indeed.co.nz", "nz": "indeed.co.nz"
    }
    
    return mapping.get(c, "indeed.com")

def _scrape_jobs_from_page(driver, base_url):
    """
    Scrapes all job links from current page.
    Returns list of unique job URLs.
    """
    job_cards = []
    selectors_to_try = [
        (By.CSS_SELECTOR, "h2.jobTitle a"),
        (By.CSS_SELECTOR, "a.jcs-JobTitle"),
        (By.CSS_SELECTOR, "a[data-jk]"),
        (By.XPATH, "//a[contains(@href, '/viewjob') or contains(@href, '/rc/clk')]"),
        (By.CSS_SELECTOR, "div.job_seen_beacon a"),
        (By.CSS_SELECTOR, "a[id^='job_']")
    ]
    
    for by, selector in selectors_to_try:
        try:
            job_cards = driver.find_elements(by, selector)
            if job_cards:
                break
        except:
            continue
    
    job_links = []
    for job in job_cards:
        try:
            href = job.get_attribute("href")
            if not href:
                continue
                
            # Extract job ID and construct clean URL
            if "jk=" in href:
                job_id = href.split("jk=")[1].split("&")[0]
                job_links.append(f"{base_url}/viewjob?jk={job_id}")
            elif "/viewjob" in href or "/rc/clk" in href:
                job_links.append(href)
        except:
            continue
    
    return list(dict.fromkeys(job_links))  # Remove duplicates

def _go_to_next_page(driver):
    """
    Attempts to click the 'Next' pagination button.
    Returns True if successful, False if no next page exists.
    """
    next_button_selectors = [
        (By.CSS_SELECTOR, "a[data-testid='pagination-page-next']"),
        (By.XPATH, "//a[@aria-label='Next Page']"),
        (By.XPATH, "//a[contains(text(), 'Next')]"),
        (By.CSS_SELECTOR, "nav[role='navigation'] a[aria-label*='Next']"),
        (By.XPATH, "//ul[@class='pagination-list']//a[@aria-label='Next']")
    ]
    
    for by, selector in next_button_selectors:
        try:
            next_btn = driver.find_element(by, selector)
            if next_btn and next_btn.is_enabled():
                driver.execute_script("arguments[0].scrollIntoView(true);", next_btn)
                time.sleep(1)
                next_btn.click()
                time.sleep(3)  # Wait for page load
                return True
        except:
            continue
    
    return False

# ------------------- Main Agent Tool -------------------

@tool
def run_headhunter_agent(job_title: str, country: str, location: str, job_limit: int):
    """
    Runs the autonomous job search with multi-page scraping.
    
    Mandatory parameters:
    - job_title: e.g. "AI Engineer"
    - country: e.g. "UAE", "USA", "UK" (Critical for correct domain)
    - location: e.g. "Dubai", "London"
    - job_limit: Number of TOP matches to return (will scan more jobs to find best matches)
    """

    # 1. VALIDATION
    if not all([job_title, country, location]):
        return "❌ Error: Missing arguments. I need Job Title, Country, and Location."
    
    if not isinstance(job_limit, int) or job_limit <= 0:
        return "❌ Error: job_limit must be a positive integer."

    # 2. PAYMENT GATE (HITL)
    cost = job_limit * 1.5
    print(f"\n✋ STOP! Checking Payment Authorization...")
    print(f"   Request: Top {job_limit} jobs x $1.5 = ${cost} Total.")

    user_decision = interrupt(f"Please approve the charge of ${cost} for analyzing jobs and returning top {job_limit} matches. (yes/no)")

    if str(user_decision).lower() not in ['yes', 'y', 'confirm', 'ok']:
        return "❌ Transaction Cancelled. User declined the payment."

    print(f"✅ Payment Approved! Starting Agent for top {job_limit} jobs...")

    # 3. SETUP
    my_resume = _read_my_resume()
    if not my_resume:
        return "❌ Error: 'resume.txt' not found."

    llm = ChatGroq(model=GROQ_MODEL, temperature=0)
    
    driver = uc.Chrome(headless=False, use_subprocess=True, version_main=143)

    try:
        # 4. DOMAIN SELECTION
        domain = _get_smart_domain(country)
        base_url = f"https://{domain}"
        
        print(f"🚀 AGENT ACTIVATED: Searching '{job_title}' in '{location}' on {base_url}...")
        
        # URL Construction
        job_query = job_title.replace(' ', '+')
        location_query = location.replace(' ', '+')
        url = f"{base_url}/jobs?q={job_query}&l={location_query}"
        
        print(f"🔗 Navigating to: {url}")
        driver.get(url)
        time.sleep(3)

        print("\n🛑 MANUAL CHECK: If Cloudflare appears, verify and wait for job listings to load.")
        print("   ⚠️  IMPORTANT: Ensure you see actual job cards on the page before continuing!")
        input("✅ Press ENTER when job listings are visible...")

        # 5. MULTI-PAGE SCRAPING
        print(f"\n🔍 Scraping jobs across multiple pages (max {MAX_PAGES_TO_SCRAPE} pages)...")
        all_job_links = []
        
        for page_num in range(1, MAX_PAGES_TO_SCRAPE + 1):
            print(f"\n📄 Scraping Page {page_num}...")
            
            page_jobs = _scrape_jobs_from_page(driver, base_url)
            
            if not page_jobs:
                print(f"   ⚠️  No jobs found on page {page_num}. Stopping pagination.")
                break
            
            print(f"   ✅ Found {len(page_jobs)} jobs on this page")
            all_job_links.extend(page_jobs)
            
            # Try to go to next page
            if page_num < MAX_PAGES_TO_SCRAPE:
                print(f"   🔄 Attempting to navigate to page {page_num + 1}...")
                if not _go_to_next_page(driver):
                    print(f"   ℹ️  No more pages available. Total pages scraped: {page_num}")
                    break
        
        # Remove duplicates
        all_job_links = list(dict.fromkeys(all_job_links))
        
        if not all_job_links:
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            return "❌ No job listings found across all pages. Page saved to 'debug_page.html'."
        
        print(f"\n✅ Total unique jobs collected: {len(all_job_links)}")
        print(f"📊 Now analyzing ALL jobs to find top {job_limit} matches...\n")

        # 6. ANALYZE ALL JOBS AND RANK
        job_scores = []  # List of (link, score, jd, analysis)
        
        for i, link in enumerate(all_job_links):
            print(f"[{i+1}/{len(all_job_links)}] Analyzing: {link[:60]}...")
            try:
                driver.get(link)
                
                wait = WebDriverWait(driver, 10)
                wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                time.sleep(2)

                # Extract job description
                jd = None
                jd_selectors = [
                    (By.ID, "jobDescriptionText"),
                    (By.CLASS_NAME, "jobsearch-jobDescriptionText"),
                    (By.CSS_SELECTOR, "div[class*='jobsearch-JobComponent-description']"),
                    (By.TAG_NAME, "body")
                ]
                
                for by, selector in jd_selectors:
                    try:
                        element = driver.find_element(by, selector)
                        jd = element.text
                        if jd and len(jd) > 100:
                            break
                    except:
                        continue
                
                if not jd or len(jd) < 100:
                    print(f"   ⚠️  Could not extract job description, skipping...")
                    continue

                # LLM Analysis
                prompt = f"""
                RESUME: {my_resume[:2000]}
                
                JOB DESCRIPTION: {jd[:2000]}
                
                TASK: Analyze how well this job matches the candidate's resume.
                FORMAT: Start with "SCORE: X%" where X is 0-100, then provide a brief summary.
                """
                
                response = llm.invoke([HumanMessage(content=prompt)]).content
                score = _extract_score(response)

                print(f"   📊 Match Score: {score}%")
                
                # Store all results
                job_scores.append({
                    'link': link,
                    'score': score,
                    'jd': jd,
                    'analysis': response
                })
            
            except Exception as e:
                print(f"   ⚠️  Error analyzing job: {e}")
                continue

        # 7. RANK AND SELECT TOP N
        if not job_scores:
            return "❌ Could not analyze any jobs successfully."
        
        # Sort by score (highest first)
        job_scores.sort(key=lambda x: x['score'], reverse=True)
        
        # Get top N matches
        top_matches = job_scores[:job_limit]
        good_matches = [j for j in top_matches if j['score'] >= MATCH_THRESHOLD]
        
        # 8. SAVE REPORT
        with open("good_jobs.txt", "w", encoding="utf-8") as f:
            f.write(f"=== HEADHUNTER REPORT: {job_title} in {location} ({country}) ===\n")
            f.write(f"Domain: {domain}\n")
            f.write(f"Total Jobs Scraped: {len(all_job_links)}\n")
            f.write(f"Total Jobs Analyzed: {len(job_scores)}\n")
            f.write(f"Top {job_limit} Matches Requested\n")
            f.write(f"Matches Above Threshold ({MATCH_THRESHOLD}%): {len(good_matches)}\n\n")
            
            for i, job in enumerate(top_matches, 1):
                f.write(f"{'='*60}\n")
                f.write(f"RANK #{i} | SCORE: {job['score']}%\n")
                f.write(f"{'='*60}\n")
                f.write(f"🔗 JOB LINK: {job['link']}\n\n")
                f.write(f"🤖 AI ANALYSIS:\n{job['analysis']}\n\n")
                f.write(f"{'-'*60}\n")
                f.write(f"📄 FULL JOB DESCRIPTION:\n{job['jd']}\n")
                f.write(f"{'='*60}\n\n")

    except Exception as e:
        return f"❌ Agent failed: {str(e)}"
    finally:
        try:
            driver.quit()
        except:
            pass

    return f"""✅ SEARCH COMPLETE!
    
📊 Analysis Summary:
- Total jobs scraped: {len(all_job_links)}
- Total jobs analyzed: {len(job_scores)}
- Top {job_limit} matches saved to 'good_jobs.txt'
- Matches above {MATCH_THRESHOLD}%: {len(good_matches)}

🏆 Best Match Score: {top_matches[0]['score']}% 
📉 Lowest in Top {job_limit}: {top_matches[-1]['score']}%

Check 'good_jobs.txt' for detailed results!"""


@tool
def read_good_jobs_report():
    """Reads the 'good_jobs.txt' file."""
    try:
        if not os.path.exists("good_jobs.txt"):
            return "❌ No job report found. Run a job search first!"
        with open("good_jobs.txt", "r", encoding="utf-8") as f:
            content = f.read()
            if not content.strip():
                return "📄 Report exists but is empty. No matching jobs were found in the last search."
            return content
    except Exception as e:
        return f"❌ Error reading report: {str(e)}"