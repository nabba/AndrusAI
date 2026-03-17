from crewai.tools import tool
import trafilatura
import requests


@tool("web_fetch")
def web_fetch(url: str) -> str:
    """
    Fetch and extract clean text content from a URL.
    Strips ads, navigation, and boilerplate. Returns plain text up to 8000 tokens.
    """
    try:
        # Try trafilatura first (best at extracting article content)
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded)
            if text:
                return text[:32000]  # ~8000 tokens

        # Fallback to requests + basic extraction
        response = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CrewAI-Bot/1.0)"},
        )
        response.raise_for_status()

        text = trafilatura.extract(response.text)
        if text:
            return text[:32000]

        # Last resort: return raw text stripped of HTML
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:32000]
    except Exception as e:
        return f"Fetch error: {str(e)}"
