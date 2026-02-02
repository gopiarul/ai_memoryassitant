import requests
import wikipedia
import random

def search_on_wikipedia(query):
    try:
        return wikipedia.summary(query, sentences=2)
    except:
        return "No result found on Wikipedia."

def get_latest_news():
    try:
        url = "https://newsapi.org/v2/top-headlines?country=in&apiKey=YOUR_API_KEY"
        res = requests.get(url).json()
        articles = res.get("articles", [])
        headlines = [a["title"] for a in articles[:5]]
        return headlines
    except:
        return ["Unable to fetch news"]

def get_random_joke():
    jokes = [
        "Why don't programmers like nature? Too many bugs.",
        "Why did Python break up with Java? Too many exceptions.",
        "I told my computer I needed a break — it froze."
    ]
    return random.choice(jokes)

def get_random_advice():
    advice_list = [
        "Practice coding every day.",
        "Read error messages carefully.",
        "Break problems into small parts."
    ]
    return random.choice(advice_list)

def get_trending_movies():
    return [
        "Oppenheimer",
        "Salaar",
        "Leo",
        "Animal",
        "Jawan"
    ]
