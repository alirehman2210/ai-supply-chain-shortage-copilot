"""Checks the Anthropic key works, with one minimal message.

    python -m scripts.verify_anthropic_key

It does two things:

  1. Looks up the configured model through the Models API, which confirms the
     model ID is real and available to this account. This is a metadata
     lookup, so no tokens are billed for it.
  2. Sends one message asking Claude to reply "OK", at low effort with a small
     output cap. This is the only token-billed request, and it is a few tokens.

It writes nothing to the database and prints no part of the key.

Failures are separated into authentication, permission, model access, and
billing or rate limiting, because the fix is different for each and the raw
provider message does not always make the difference obvious.

Exit code 0 means the key authenticated and the model answered.
"""

import os
import sys

PROMPT = "Reply with exactly: OK"
MAX_TOKENS = 128


def _needs_workspace_header(message: str) -> bool:
    """True when the failure is only that the key is organisation-scoped.

    An API key created at the organisation level, rather than inside a
    workspace, has to say which workspace a request belongs to. That is a
    request-shape problem, not a bad key: the request got far enough to be
    understood, which means authentication already succeeded.
    """
    lowered = message.lower()
    return "anthropic-workspace-id" in lowered or "scoped to a workspace" in lowered


def _looks_like_billing(message: str) -> bool:
    lowered = message.lower()
    return any(
        term in lowered
        for term in ("credit balance", "billing", "quota", "insufficient", "payment")
    )


def main() -> int:
    from src.config import CLAUDE_MODEL, MissingCredential, redact, require_api_key

    print("Anthropic key check")
    print("-" * 60)
    print(f"  Model configured : {CLAUDE_MODEL}")
    print("  Read from        : ANTHROPIC_API_KEY (via .env)")

    try:
        require_api_key("ANTHROPIC_API_KEY")
    except MissingCredential as error:
        # No request is made, so nothing is spent.
        print("\n  NOT CONFIGURED — no API call was made.\n")
        print("  " + str(error).replace("\n", "\n  "))
        return 1

    print("  Key              : configured (value not shown)")

    try:
        import anthropic
    except ImportError as error:
        print(f"\n  FAILED — dependency missing ({error.name}).")
        print("  Run: pip install -r requirements.txt")
        return 1

    # The key is read from the environment by the client. It is never passed
    # here, and never held in a local variable.
    workspace_id = os.getenv("ANTHROPIC_WORKSPACE_ID", "").strip()
    if workspace_id:
        print("  Workspace        : set via ANTHROPIC_WORKSPACE_ID")
        client = anthropic.Anthropic(
            default_headers={"anthropic-workspace-id": workspace_id}
        )
    else:
        client = anthropic.Anthropic()

    def explain(error: Exception) -> None:
        """Print a credential-safe diagnosis. redact() strips key-shaped text."""
        message = redact(error)
        print(f"  Provider said    : {message[:300]}")

    # --- Step 1: is the model ID real and available to this account? --------
    print(f"\n  Checking the model ID is supported (no tokens billed)...")
    try:
        model = client.models.retrieve(CLAUDE_MODEL)
        print(f"  OK — {model.id} ({getattr(model, 'display_name', 'n/a')})")
    except anthropic.AuthenticationError as error:
        print("\n  FAILED — AUTHENTICATION. The key was rejected (401).")
        explain(error)
        print("\n  The key is wrong, revoked, or from another account.")
        print("  Check it in .env against https://console.anthropic.com/settings/keys")
        return 1
    except anthropic.PermissionDeniedError as error:
        print("\n  FAILED — PERMISSION. The key is valid but not allowed (403).")
        explain(error)
        print("\n  The key may be scoped to a workspace that excludes this model.")
        return 1
    except anthropic.NotFoundError as error:
        print(f"\n  FAILED — MODEL ACCESS. '{CLAUDE_MODEL}' was not found (404).")
        explain(error)
        print("\n  Either the model ID is wrong, or this account cannot use it.")
        print("  Set CLAUDE_MODEL in .env to a model your account has access to.")
        return 1
    except anthropic.APIConnectionError as error:
        print("\n  FAILED — could not reach the API. Network or DNS problem.")
        explain(error)
        return 1
    except anthropic.APIStatusError as error:
        message = redact(error)
        if _needs_workspace_header(message):
            print("  SKIPPED — this key is organisation-scoped, so the Models")
            print("  API needs a workspace id. Authentication itself worked.")
            print("  Set ANTHROPIC_WORKSPACE_ID in .env to check model access,")
            print("  or ignore this: the message request below is the real test.")
        else:
            print(f"\n  FAILED — unexpected API error (HTTP {error.status_code}).")
            explain(error)
            return 1

    # --- Step 2: the one billed request -------------------------------------
    print(f"\n  Sending one minimal message (max_tokens={MAX_TOKENS}, effort low)...")
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            # Low effort keeps thinking short, so this stays a few tokens.
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": PROMPT}],
        )
    except anthropic.AuthenticationError as error:
        print("\n  FAILED — AUTHENTICATION on the message request (401).")
        explain(error)
        return 1
    except anthropic.PermissionDeniedError as error:
        print("\n  FAILED — PERMISSION denied for this request (403).")
        explain(error)
        return 1
    except anthropic.NotFoundError as error:
        print("\n  FAILED — MODEL ACCESS denied for messages (404).")
        explain(error)
        return 1
    except anthropic.RateLimitError as error:
        message = redact(error)
        if _looks_like_billing(message):
            print("\n  FAILED — BILLING. The key works, but there is no credit.")
            print("  Top up at https://console.anthropic.com/settings/billing")
        else:
            print("\n  FAILED — RATE LIMIT. The key works; too many requests.")
            print("  Wait a moment and run this again.")
        explain(error)
        return 1
    except anthropic.BadRequestError as error:
        message = redact(error)
        if _needs_workspace_header(message):
            print("\n  FAILED — WORKSPACE SCOPE. The key authenticated, but it is")
            print("  an organisation-level key and this request needs a workspace.")
            print("\n  Two ways to fix it:")
            print("    a) create a workspace-scoped key in the Anthropic console")
            print("       and put that in .env instead, or")
            print("    b) add ANTHROPIC_WORKSPACE_ID=<workspace id> to .env")
            print("\n  Either works with the workflow: the model client passes")
            print("  ANTHROPIC_WORKSPACE_ID through when it is set.")
            explain(error)
            return 1
        if _looks_like_billing(message):
            print("\n  FAILED — BILLING. The key works, but credit is too low.")
            print("  Top up at https://console.anthropic.com/settings/billing")
        else:
            print("\n  FAILED — the request itself was rejected (400).")
            print("  This is a problem with the request, not your key.")
        explain(error)
        return 1
    except anthropic.APIConnectionError as error:
        print("\n  FAILED — could not reach the API. Network or DNS problem.")
        explain(error)
        return 1
    except anthropic.APIStatusError as error:
        print(f"\n  FAILED — unexpected API error (HTTP {error.status_code}).")
        explain(error)
        return 1

    # --- Report what came back ----------------------------------------------
    if response.stop_reason == "refusal":
        print("\n  The key works, but the model declined to answer.")
        print("  Authentication is fine; this prompt was refused.")
        return 1

    # Thinking blocks come back with empty text by default, so collect the
    # text blocks rather than assuming content[0] is the answer.
    reply = " ".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ).strip()

    print(f"  OK — the model answered.")
    print(f"\n  Model used       : {response.model}")
    print(f"  Authentication   : worked")
    print(f"  Reply            : {reply!r}")
    print(f"  Stop reason      : {response.stop_reason}")
    print(
        f"  Tokens billed    : {response.usage.input_tokens} in, "
        f"{response.usage.output_tokens} out"
    )

    print("\n  PASS — the Anthropic API is working for this project.")
    print("  Nothing was written to the database and no workflow was run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
