from duckduckgo_search import DDGS

def ai_reply(query):
    with DDGS() as ddgs:
        results = ddgs.text(
            query,
            max_results=1
        )
        for r in results:
            return r["body"]

while True:
    q = input("You: ")
    if q.lower() == "exit":
        break
    print("AI:", ai_reply(q))
