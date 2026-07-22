import sys
from formatter import format_json

from chat_history import (
    append_assistant_message,
    append_user_message,
    create_chat_file,
    load_messages,
)
from config_loader import load_config
from prompt_loader import load_prompt
from provider_client import load_provider


def main() -> None:
    """Run the orchestrator terminal application.

    Args:
        None.

    Returns:
        None.
    """
    config = load_config()
    system_prompt = load_prompt(config.prompt_path)

    provider = load_provider(config.provider, config.allowed_providers)
    client = provider.create_client(config)

    print(f"Loading {config.provider} model {config.model}...")
    provider.preload(config, client)

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
            content = ""
            response = provider.send_request(
                config=config,
                client=client,
                system_prompt=system_prompt,
                messages=messages,
            )
            content = provider.extract_content(response)
            formatted = format_json(content)

            append_user_message(chat_file, query)
            append_assistant_message(chat_file, content)
            print(f"\n{formatted}")

        except Exception as e:
            print(f"\nError: {e}", file=sys.stderr)
            if content:
                print(f"Raw response: {content!r}", file=sys.stderr)


if __name__ == "__main__":
    main()
