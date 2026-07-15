import sys
from formatter import format_json

from chat_history import (append_assistant_message, append_user_message,
                          create_chat_file, load_messages)
from config_loader import load_config
from ollama_client import extract_content, preload_model, send_request
from prompt_loader import load_prompt


def main() -> None:
    """Run the orchestrator terminal application.

    Args:
        None.

    Returns:
        None.
    """
    config = load_config()
    system_prompt = load_prompt(config.prompt_path)

    print(f"Loading model {config.model}...")
    preload_model(config.ollama_endpoint, config.model, config.request_timeout)

    chat_file = create_chat_file(config.chat_directory, system_prompt)

    print("KIO1 Orchestrator (type 'exit' to quit)")
    print("-" * 40)

    while True:
        try:
            query = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not query:
            continue

        if query.lower() == "exit":
            print("Goodbye.")
            break

        messages = load_messages(chat_file)
        messages.append({"role": "user", "content": query})

        try:
            response = send_request(
                endpoint=config.ollama_endpoint,
                model=config.model,
                system_prompt=system_prompt,
                messages=messages,
                temperature=config.temperature,
                timeout=config.request_timeout,
            )
            content = extract_content(response)
            formatted = format_json(content)

            append_user_message(chat_file, query)
            append_assistant_message(chat_file, content)
            print(f"\n{formatted}")

        except Exception as e:
            print(f"\nError: {e}", file=sys.stderr)
            if "content" in dir() and content:
                print(f"Raw response: {content!r}", file=sys.stderr)


if __name__ == "__main__":
    main()
