#
# Paperless-ngx AI on the Claude plan: Claude titles and files new documents.
#
# Paperless's AI speaks only the OpenAI or Ollama API, and the Claude plan
# (Max, kirill's login under ~kirill/.claude) is usable only through Claude
# Code. paperless-claude bridges the two: an OpenAI-style endpoint on loopback
# that answers with `claude -p` (see paperless-claude.py for the details).
#
# What uses it: the workflow "Claude files new documents" (Document Added,
# every source), created in the web UI / API, not here: Workflows are database
# state. Its actions, in order:
#   1. assign owner kirill: workflows act as the document's owner, and every
#      correspondent, type and tag is owned by kirill, so an unowned scan
#      would get no candidates and fresh duplicates;
#   2. AI: title, overwriting (a new document's title is just its file name);
#   3. AI: correspondent if none matched, creating it when new;
#   4. AI: document type and tags, existing ones only; the matching rules'
#      picks stay, AI tags are added to them.
# The filename follows the title and correspondent (PAPERLESS_FILENAME_FORMAT).
# The three AI actions cost one `claude` run per document (cache in the
# bridge). Each document page also gets an "AI suggestions" button.
#
# The workflow runs once per document. When the bridge has no answer at that
# moment (the Claude plan's weekly limit is used up), the document keeps its
# file name as title; paperless-retitle asks again once a day for every such
# document of the last 30 days and applies what the workflow would have
# (paperless-retitle.py). A run that finds none asks Claude nothing.
#
# Logs: journalctl -u paperless-claude (one line per Claude run or cache hit),
# journalctl -u paperless-retitle (one line per document it filed) and the
# task list in the web UI (task type "Apply AI Suggestions").
#
{
  config,
  inputs,
  pkgs,
  ...
}:
let
  claudeCode = inputs.llm-agents.packages.${pkgs.stdenv.hostPlatform.system}.claude-code;
  bridgePort = 8794; # loopback
  paperlessUrl = "http://127.0.0.1:${toString config.services.paperless.port}";
  paperlessUser = "kirill";
  passwordCredential = "paperless-password:${config.sops.secrets."paperless/admin-password".path}";

  bridge = pkgs.writers.writePython3Bin "paperless-claude" { flakeIgnore = [ "E501" ]; } (
    builtins.readFile ./paperless-claude.py
  );

  retitle = pkgs.writers.writePython3Bin "paperless-retitle" { } (
    builtins.readFile ./paperless-retitle.py
  );
in
{
  services.paperless.settings = {
    PAPERLESS_AI_ENABLED = true;
    PAPERLESS_AI_LLM_BACKEND = "openai-like";
    PAPERLESS_AI_LLM_ENDPOINT = "http://127.0.0.1:${toString bridgePort}/v1";
    # Only a label here; the bridge picks the model.
    PAPERLESS_AI_LLM_MODEL = "claude";
    # Requests queue behind one `claude` at a time; a scanned stack of ten
    # documents takes a few minutes.
    PAPERLESS_AI_LLM_REQUEST_TIMEOUT = 900;
  };

  systemd.services.paperless-claude = {
    description = "OpenAI-style bridge from Paperless AI to Claude Code";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];
    path = [ claudeCode ];
    environment = {
      PORT = toString bridgePort;
      PAPERLESS_URL = paperlessUrl;
      PAPERLESS_USER = paperlessUser;
      CLAUDE_MODEL = "sonnet";
    };
    serviceConfig = {
      ExecStart = "${bridge}/bin/paperless-claude";
      # kirill's Claude login. Loopback only, and `claude` runs without tools,
      # so a caller gets text answers on the plan and nothing else.
      User = "kirill";
      # For the list of existing correspondents, types and tags.
      LoadCredential = passwordCredential;
      # Empty working directory for `claude`.
      RuntimeDirectory = "paperless-claude";
      Restart = "always";
      RestartSec = 10;
      # One `claude` at a time, ~300 MB.
      MemoryHigh = "600M";
      MemoryMax = "1G";
    };
  };

  systemd.services.paperless-retitle = {
    description = "Claude files the Paperless documents it could not when they arrived";
    after = [
      "paperless-claude.service"
      "paperless-web.service"
    ];
    environment = {
      PAPERLESS_URL = paperlessUrl;
      PAPERLESS_USER = paperlessUser;
      BRIDGE_URL = "http://127.0.0.1:${toString bridgePort}/v1/chat/completions";
    };
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${retitle}/bin/paperless-retitle";
      # It only talks to Paperless and the bridge, both on loopback.
      DynamicUser = true;
      LoadCredential = passwordCredential;
      IPAddressDeny = "any";
      IPAddressAllow = "localhost";
    };
  };

  # Half an hour after the weekly limit resets (Monday 21:00 UTC), so the
  # documents of an outage are filed the evening it ends.
  systemd.timers.paperless-retitle = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "*-*-* 21:30:00 UTC";
      Persistent = true;
    };
  };

  sops.secrets."paperless/admin-password".restartUnits = [ "paperless-claude.service" ];
}
