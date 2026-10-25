# MCP and document RAG — first syllabus increment

All paths below are under the configurable PRIMUS root. The running tray/voice clients share the authenticated local API; restart PRIMUS after changing its configuration.

## 1. Local RAG memory

RAG means retrieve relevant document passages and supply them to the language model before answering. This implementation uses **SQLite FTS5 BM25 lexical retrieval**, not a dense embedding model. It adds no dependency for UTF-8 text documents and makes no model download. Approved facts/preferences remain in their existing memory database; document text lives separately in `data/knowledge.sqlite3`.

### Windows commands

Use the Python runtime already used by this laptop:

```powershell
$primusPython = "C:\Users\Subhajeet Bose\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$primusRoot = "C:\Users\Subhajeet Bose\Downloads\Primus"
& $primusPython "$primusRoot\primus.py" knowledge index "$primusRoot\outputs\notes.md"
& $primusPython "$primusRoot\primus.py" knowledge list
& $primusPython "$primusRoot\primus.py" knowledge search "release date"
& $primusPython "$primusRoot\primus.py" knowledge ask "What release date is stated in my indexed notes?"
```

Indexing first shows the full source path, SHA-256, chunk count, storage destination and replacement/persistence effect. Type `y` only after reviewing it. The API/tray/voice tool path displays the signed-in user's normal exact-action approval dialog. This increment does not add an indexing card to Mission Control. No source is indexed merely by opening this guide.

On macOS/Linux, use the same commands with your Python executable and moved/configured root, for example `python3 "/path with spaces/Primus/primus.py" knowledge list`. SQLite must include FTS5. No OS-specific vector service is required.

After `knowledge list` returns a document ID, inspect or forget it:

```powershell
& $primusPython "$primusRoot\primus.py" knowledge read "<document-id>" 1
& $primusPython "$primusRoot\primus.py" knowledge forget "<document-id>"
```

Forgetting requires fresh exact approval and removes indexed passages; it preserves the source file. It is not a guarantee of forensic secure erasure. To edit document knowledge, edit the source through the normal approved workflow and explicitly index it again. Changed, missing or no-longer-approved source paths are excluded from search/read until an authorized current version is indexed. File hashes pin versions; they do not validate whether the document's claims are true.

### Natural-language and API access

- Use `knowledge: What does the release guide say about deployment?` or `rag: <question>` through `ask`, a tray task or the local API. The explicit prefix always retrieves before generation and avoids the fast general-knowledge route.
- Natural-language requests also have `knowledge_index`, `knowledge_list`, `knowledge_search`, `knowledge_read` and `knowledge_forget` plugins. The model is instructed to retrieve before making claims about indexed sources; the explicit prefix is the deterministic route when grounding matters.
- Answers carry labels such as `[K:document-id:chunk]` and a source list with paths, hashes and PDF page numbers when available. Unsupported or missing labels cause one model retry, then failure rather than a claimed grounded answer. Label validation checks provenance, not semantic entailment or correctness.
- Bearer-authenticated `GET /knowledge` lists sources. `POST /knowledge/search` and `POST /knowledge/ask` accept `{"question":"..."}`. Existing Mission Control cookies do not authorize these broader knowledge endpoints.
- Index/reindex/forget actions go through normal CLI/plugin permission gates, not an unauthenticated upload endpoint. Retrieved data and source instructions cannot grant tool authority. Transcript capture and automatic preference learning remain disabled.

### Formats and limits

- UTF-8 `.txt`, `.md` and `.rst`; text `.pdf` only when the chosen Python runtime provides the optional `pypdf` package. This laptop's existing bundled runtime has it; it was not newly installed by this increment.
- At most 1 MB per source, 50 PDF pages, 200 overlapping chunks per source and 100 indexed documents. Chunks are approximately 1,000 characters with 120-character overlap. Search retrieves up to four passages by default (internal maximum six).
- Queries are bounded, tokenized and passed as SQL parameters; user text is not used as raw FTS syntax. Retrieval is lexical and can miss paraphrases or synonyms. No OCR, scanned-image extraction, dense embeddings, automatic folder monitoring or guaranteed defense against malicious document parsers is claimed.
- Credential paths are blocked and possible key/password patterns are refused. This is not a complete secret detector. Review the document before approving persistence.
- No matching current passage means no generated answer. The index is local; the current model endpoint remains localhost. Documents/questions/passages are not uploaded or used to change model weights. Audit logs redact content and queries.

## 2. Local MCP tool connections

The client implements the [2025-11-25 MCP lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle), [Streamable HTTP transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) and [tools operations](https://modelcontextprotocol.io/specification/2025-11-25/server/tools). It supports initialization, initialized notification, session/version headers, bounded paginated `tools/list`, `tools/call`, JSON and SSE response bodies, and session cleanup. Its advertised client capabilities are empty: no sampling, elicitation or filesystem roots are granted.

The first bridge increment used an empty server list. After explicit user approval on 22 October 2026, this laptop now enables the reviewed first-party `primus_local` server described below. Third-party servers still require separate review and configuration.

### Configure a separately reviewed server

The first increment accepts only already-running local HTTP endpoints. A disabled example row is:

```json
"mcp": {
  "enabled": true,
  "servers": [
    {
      "name": "example",
      "url": "http://127.0.0.1:8767/mcp",
      "enabled": false,
      "network": true,
      "secret_name": "mcp-example-token"
    }
  ]
}
```

Do not enable an unknown server. Review its code/capabilities and exact config change first. `network` defaults to true; such a server is blocked while `network_plugins_enabled=false`. Declare `network=false` only for a server you have reviewed as local-only. That declaration does not enforce an OS network namespace. PRIMUS's network-isolated script sandbox does not contain a separately running MCP server.

If the server uses a bearer token, use the prompted OS-keychain command (never place the value in JSON, arguments or logs):

```powershell
& $primusPython "$primusRoot\primus.py" secret-set "mcp-example-token"
& $primusPython "$primusRoot\primus.py" mcp servers
& $primusPython "$primusRoot\primus.py" mcp tools "example"
```

The optional `secret_name` refers to a `PRIMUS-local` OS-keychain account. Its token is sent only to the configured localhost endpoint; redirects and system proxies are disabled. Use a dedicated server token, not the PRIMUS API token. Token creation still asks for exact confirmation. The built-in local status server uses its own approved `mcp-primus-token` keychain item; third-party servers need separate credentials.

When a server advertises a tool, invoke it using the discovered schema. For an example `sum_numbers` tool accepting `values`:

```powershell
'{"values":[2,4,6]}' | & $primusPython "$primusRoot\primus.py" mcp call "example" "sum_numbers" --arguments -
```

Every call is EXTERNAL, including tools annotated read-only. The prompt shows the server/endpoint, tool, exact arguments and a reviewed hash. After approval, PRIMUS reconnects, checks the advertised definition and arguments, and invokes the tool once. Changed metadata or inputs require fresh approval. This cannot detect an implementation change hidden behind unchanged server metadata. A response is untrusted data and is not independent proof of an external action's success. Connection loss can leave the outcome unknown; calls are not automatically retried. Cancellation prevents subsequent calls but cannot undo an action already started by the separate server.

The core plugins are `mcp_servers`, `mcp_list` and `mcp_call`. For example, ask PRIMUS to list configured MCP servers, discover one server's tools and carry out a named action. Tool schemas are discovered at use time; they are not installed as arbitrary Python code or treated as trusted instructions.

### Supported subset and limits

- Pinned protocol `2025-11-25`; newer-only servers need a compatible endpoint/version. Remote HTTP(S), stdio subprocesses and legacy HTTP+SSE are deferred.
- Up to four explicitly configured servers; at most 40 tools over four catalog pages; catalog at most 24 KB. Tool inputs are object JSON schemas with bounded nesting, scalar/nullable types, properties, required, additionalProperties, arrays/items, enum and basic numeric/string/array length bounds. Schemas using `$ref`, combinators or unsupported validation keywords fail closed with a clear error. This is not full JSON Schema support.
- Arguments at most 16 KB; response transport at most 256 KB; returned tool result at most 12 KB. Text/structured JSON content is returned as untrusted data; no resource/image URL is fetched automatically.
- Ten-second request/read deadlines, bounded bodies and no automatic tool retries. Long-running task-based execution, server-initiated requests, subscriptions and automatic tools-changed updates are deferred. Definitions are rediscovered before a call instead.
- The server remains responsible for its own authentication, Origin validation, filesystem/network restrictions and actual tool effects. Review separately trusted server software before enabling it.

## 3. Verification and next syllabus steps

The increment adds permission/version/revocation/citation tests and actual localhost JSON-RPC transport tests, including JSON and SSE. Test-only consent callbacks and credentials are synthetic fixtures, not records of user approval or certification against every third-party MCP SDK.

Run `python scripts/demo-knowledge.py` to perform three RAG answers with the real configured local model against an isolated synthetic corpus. It saves a report under `outputs/knowledge-demos` and fixture files under `temp`, never personal facts or production document memory. Corpus construction in this test helper is synthetic setup, not a claimed human approval event. The three demos cover one-source recall, an exact dataset identifier and an answer requiring two sources.

This completes the initial MCP tools bridge and lexical document-RAG increment. Dense semantic embeddings, wider MCP transports/schema compatibility, formal planning, named agent frameworks and benchmark evaluation remain separate future increments. Checkpoint changes in `decision.md` before beginning the next one.


## 4. Approved first party local status server

This laptop enables `local_mcp` on `http://127.0.0.1:8767/mcp` and a matching `primus_local` client entry. Its dedicated bearer token is stored only in OS keychain service `PRIMUS-local`, account `mcp-primus-token`. The normal tray or `serve` lifecycle owns the server; no additional startup task was registered. `python primus.py mcp-serve` is available for standalone use when the tray/core is not already owning that port. Never run two instances on the same port.

The only exposed tool is `primus_status`, with exact arguments `{}`. It reads the configured model/root, indexed document/passage counts, voice state, process ID and timestamp. It does not read source passages or credentials, launch programs or change settings. Authentication, loopback Host/Origin checks, bounded sessions/bodies, version checks and the global kill switch protect the endpoint. The server offers the synchronous tools subset; unsolicited SSE events, resources and prompts are not implemented.

Using the Python/root variables above, discovery and a separately approved call are:

```powershell
& $primusPython "$primusRoot\primus.py" mcp tools "primus_local"
'{}' | & $primusPython "$primusRoot\primus.py" mcp call "primus_local" "primus_status" --arguments -
```

Discovery does not invoke a tool. Each call still requires its own exact EXTERNAL approval, even though this tool is read-only. The user's setup approval authorized one test only and does not grant ongoing automatic tool access.

The approved production test succeeded on 22 October 2026: one real MCP call reported one indexed document, 19 passages and voice `listening`. Root/model/counts were independently compared with local configuration and the read-only database, and exactly one server tool audit entry was observed. Evidence: `outputs/mcp-local-live-verification.json`. All 159 tests passed before production enablement. These checks do not certify interoperability with every MCP client.

To disable this server after reviewing the config change, set `local_mcp.enabled=false` and disable the `primus_local` row under `mcp.servers`, then restart the existing PRIMUS tray. The dedicated keychain item remains unless separately approved for removal. Tokens must never be pasted into JSON or logs.
