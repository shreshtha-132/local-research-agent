"""Command-line version of the research agent.

Usage:
    python research_agent.py "what's new in open-source LLMs"
    python research_agent.py            # asks for a prompt
    python research_agent.py --help
"""

import argparse
import sys

import ollama

import agent


def main():
    parser = argparse.ArgumentParser(description="Search the web and summarize it with a local LLM.")
    parser.add_argument("query", nargs="*", help="what to research (prompted if omitted)")
    parser.add_argument("--model", default=agent.DEFAULT_MODEL, help=f"Ollama model (default {agent.DEFAULT_MODEL})")
    parser.add_argument("--results", type=int, default=agent.DEFAULT_RESULTS, help="number of search results")
    parser.add_argument("--max-chars", type=int, default=agent.DEFAULT_MAX_CHARS, help="max characters kept per page")
    parser.add_argument("--num-ctx", type=int, default=agent.DEFAULT_NUM_CTX, help="model context window in tokens")
    parser.add_argument("--think", action="store_true", help="let the model reason first (slower)")
    args = parser.parse_args()

    query = " ".join(args.query).strip() or input("Enter your prompt: ").strip()
    if not query:
        sys.exit("Prompt cannot be empty.")

    print(f"Searching: {query}")
    sources = agent.search_web(query, args.results)
    if not sources:
        sys.exit("No search results. DuckDuckGo may be rate-limiting you; wait a minute and retry.")

    for s in agent.fetch_all(sources, args.max_chars):
        status = f"ok, {len(s.text):,} chars" if s.ok else f"skipped: {s.error}"
        print(f"  [{s.n}] {s.url}  ({status})")

    print(f"\nSummarizing with {args.model}...\n")
    body = ""
    try:
        for chunk in agent.stream_digest(query, sources, args.model, args.num_ctx, args.think):
            print(chunk, end="", flush=True)
            body += chunk
    except ConnectionError:
        sys.exit("\nCan't reach Ollama. Is the Ollama app running?")
    except ollama.ResponseError as e:
        sys.exit(f"\nOllama error: {e.error}  (try: ollama pull {args.model})")

    path = agent.save_digest(query, agent.format_digest(query, body, sources, args.model))
    print(f"\n\nSaved to {path}")


if __name__ == "__main__":
    main()
