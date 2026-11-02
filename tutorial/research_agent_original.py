
import json
import requests
import ollama
from bs4 import BeautifulSoup
from datetime import datetime
from ddgs import DDGS

MODEL = "qwen3.5:4b"

# Search web using DuckDuckGo (free, no API key)
def search_web(query):
    results = DDGS().text(query, max_results=5)
    return [
        {"title": r.get("title", ""), "url": r.get("href"), "content": r.get("body", "")}
        for r in results
    ]

# Fetch full web page content
def fetch_text(url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        return ""
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def main():
    user_prompt = input("Enter your prompt: ").strip()
    if not user_prompt:
        print("Prompt cannot be empty.")
        return

    results = search_web(user_prompt)

    # For each url in web search result, fetch full content
    pages = []
    for item in results:
        url = item.get("url")
        if not url:
            continue

        print(f"Fetching: {url}")
        page_text = fetch_text(url)

        pages.append({
            "title": item.get("title", ""),
            "url": url,
            "snippet": item.get("content", ""),
            "page_text": page_text,
        })

    # Prompt to send to Qwen model with web data
    prompt = f"""
    User request:
    {user_prompt}

    Use these web results and page contents to answer in markdown format.

    Data:
    {json.dumps(pages, ensure_ascii=False)}
    """

    # Invoke local Qwen model
    response = ollama.chat(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
    )

    digest = response.message.content

    # Build a unique filename using today's date and time
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"digest-{timestamp}.md"

    # Save the digest to disk
    with open(filename, "w") as f:
        f.write(digest)

    print(f"Saved to {filename}")

if __name__ == "__main__":
    main()