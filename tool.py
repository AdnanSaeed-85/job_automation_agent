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
MAX_PAGES_TO_SCRAPE = 5 

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
    c = country_input.lower().strip()
    mapping = {
    # North America
    "usa": "indeed.com",
    "us": "indeed.com",
    "united states": "indeed.com",
    "canada": "ca.indeed.com",
    "mexico": "mx.indeed.com",

    # Europe
    "uk": "indeed.co.uk",
    "united kingdom": "indeed.co.uk",
    "ireland": "ie.indeed.com",
    "germany": "de.indeed.com",
    "france": "fr.indeed.com",
    "italy": "it.indeed.com",
    "spain": "es.indeed.com",
    "netherlands": "nl.indeed.com",
    "sweden": "se.indeed.com",
    "norway": "no.indeed.com",
    "denmark": "dk.indeed.com",
    "switzerland": "ch.indeed.com",
    "austria": "at.indeed.com",
    "belgium": "be.indeed.com",
    "poland": "pl.indeed.com",
    "portugal": "pt.indeed.com",
    "romania": "ro.indeed.com",
    "czech republic": "cz.indeed.com",
    "finland": "fi.indeed.com",

    # Middle East
    "uae": "ae.indeed.com",
    "dubai": "ae.indeed.com",
    "saudi arabia": "sa.indeed.com",
    "qatar": "qa.indeed.com",
    "kuwait": "kw.indeed.com",
    "oman": "om.indeed.com",
    "bahrain": "bh.indeed.com",
    "egypt": "eg.indeed.com",

    # Asia
    "india": "in.indeed.com",
    "pakistan": "pk.indeed.com",
    "bangladesh": "bd.indeed.com",
    "sri lanka": "lk.indeed.com",
    "singapore": "sg.indeed.com",
    "malaysia": "my.indeed.com",
    "philippines": "ph.indeed.com",
    "indonesia": "id.indeed.com",
    "japan": "jp.indeed.com",
    "south korea": "kr.indeed.com",
    "china": "cn.indeed.com",
    "hong kong": "hk.indeed.com",
    "taiwan": "tw.indeed.com",
    "thailand": "th.indeed.com",
    "vietnam": "vn.indeed.com",

    # Africa
    "south africa": "za.indeed.com",
    "nigeria": "ng.indeed.com",
    "kenya": "ke.indeed.com",
    "ghana": "gh.indeed.com",
    "morocco": "ma.indeed.com",
    "tunisia": "tn.indeed.com",

    # Oceania
    "australia": "au.indeed.com",
    "new zealand": "nz.indeed.com",
}

    return mapping.get(c, "indeed.com")

def _scrape_jobs_from_page(driver, base_url):
    job_cards = []
    selectors_to_try = [
        (By.CSS_SELECTOR, "h2.jobTitle a"), (By.CSS_SELECTOR, "a.jcs-JobTitle"),
        (By.CSS_SELECTOR, "a[data-jk]"), (By.XPATH, "//a[contains(@href, '/viewjob')]"),
        (By.CSS_SELECTOR, "div.job_seen_beacon a")
    ]
    
    for by, selector in selectors_to_try:
        try:
            job_cards = driver.find_elements(by, selector)
            if job_cards: break
        except: continue
    
    job_links = []
    for job in job_cards:
        try:
            href = job.get_attribute("href")
            if href:
                if "jk=" in href:
                    job_id = href.split("jk=")[1].split("&")[0]
                    job_links.append(f"{base_url}/viewjob?jk={job_id}")
                elif "/viewjob" in href:
                    job_links.append(href)
        except: continue
    return list(dict.fromkeys(job_links))

def _go_to_next_page(driver):
    selectors = [
        (By.CSS_SELECTOR, "a[data-testid='pagination-page-next']"),
        (By.XPATH, "//a[@aria-label='Next Page']")
    ]
    for by, selector in selectors:
        try:
            btn = driver.find_element(by, selector)
            btn.click()
            time.sleep(3)
            return True
        except: continue
    return False

# ------------------- Main Agent Tool -------------------

@tool
def run_headhunter_agent(job_title: str, country: str, location: str, job_limit: int):
    """
    Runs the autonomous job search. 
    """
    if not all([job_title, country, location]):
        return "❌ Error: Missing arguments."

    # --- 1. PAYMENT GATE ---
    cost = job_limit * 1.5
    user_decision = interrupt(f"Approve charge of ${cost} for {job_limit} jobs?")

    if str(user_decision).lower() not in ['yes', 'y', 'confirm', 'ok']:
        return "❌ Search Cancelled by User."

    # --- 2. START AUTOMATION ---
    my_resume = _read_my_resume()
    if not my_resume: return "❌ Error: 'resume.txt' not found."

    llm = ChatGroq(model=GROQ_MODEL, temperature=0)
    
    options = uc.ChromeOptions()
    # options.add_argument('--headless=new') # Uncomment for Server Deployment
    
    # 🛑 ROBUST DRIVER FIX: Auto-detect first, force v143 if that fails
    try:
        driver = uc.Chrome(options=options, use_subprocess=True)
    except Exception as e:
        print(f"⚠️ Auto-version failed. Forcing Chrome v143. Error: {e}")
        driver = uc.Chrome(options=options, use_subprocess=True, version_main=143)

    try:
        domain = _get_smart_domain(country)
        base_url = f"https://{domain}"
        url = f"{base_url}/jobs?q={job_title.replace(' ', '+')}&l={location.replace(' ', '+')}"
        
        driver.get(url)
        
        # 🛑 FIX: NO INPUT(). JUST WAIT.
        print("⏳ Waiting 15 seconds for Page Load...")
        time.sleep(15) 
        
        all_job_links = []
        for _ in range(MAX_PAGES_TO_SCRAPE):
            links = _scrape_jobs_from_page(driver, base_url)
            all_job_links.extend(links)
            if len(all_job_links) >= job_limit * 2: break 
            if not _go_to_next_page(driver): break
        
        # Deduplicate and limit
        all_job_links = list(set(all_job_links))[:job_limit]
        
        if not all_job_links:
            return "❌ No jobs found. Indeed might have blocked the browser."

        # Analyze
        good_matches = 0
        with open("good_jobs.txt", "w", encoding="utf-8") as f:
            f.write(f"=== REPORT: {job_title} in {location} ===\n\n")

        results_summary = []

        for link in all_job_links:
            try:
                driver.get(link)
                time.sleep(2)
                
                # Try finding description
                try:
                    jd = driver.find_element(By.ID, "jobDescriptionText").text
                except:
                    jd = driver.find_element(By.TAG_NAME, "body").text

                prompt = f"RESUME: {my_resume[:2000]}\nJOB: {jd[:2000]}\nGive me a 0-100% match score. Format: SCORE: X%"
                response = llm.invoke([HumanMessage(content=prompt)]).content
                score = _extract_score(response)
                
                # Store all results
                if score >= MATCH_THRESHOLD:
                    good_matches += 1
                    with open("good_jobs.txt", "a", encoding="utf-8") as f:
                        f.write(f"LINK: {link}\nSCORE: {score}%\nAI: {response}\n{'-'*50}\n")
                    results_summary.append(f"✅ Match ({score}%): {link}")
                    
            except: continue

    except Exception as e:
        return f"❌ Error: {str(e)}"
    finally:
        driver.quit()

    return f"✅ Done! Found {good_matches} matches. \n" + "\n".join(results_summary)

@tool
def read_good_jobs_report():
    """Reads the 'good_jobs.txt' file."""
    if os.path.exists("good_jobs.txt"):
        # 🛑 FIX: Added encoding="utf-8" to prevent Windows crash
        with open("good_jobs.txt", "r", encoding="utf-8") as f: 
            return f.read()
    return "No report found."