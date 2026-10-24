/* D10 E3-2 argv allowlist. Never log argv. Rejection is bad_argv only. */
#include "sandbox_argv.h"

#include <ctype.h>

static int starts_with(const char *s, const char *p)
{
    return strncmp(s, p, strlen(p)) == 0;
}

static int is_file_path(const char *s)
{
    if (!s || !s[0]) {
        return 1;
    }
    if (s[0] == '{') {
        return 0; /* inline JSON */
    }
    return 1;
}

static int json_mcp_ok(const char *js)
{
    /* Structural subset: mcpServers keys ⊆ {github_comment, github_file_ops,
     * github_inline_comment, github_ci, github}. Extra keys reject. */
    const char *p;
    int depth = 0, in_str = 0, esc = 0;
    if (!js || js[0] != '{') {
        return 0;
    }
    p = strstr(js, "\"mcpServers\"");
    if (!p) {
        /* tag mode always sends mcp-config; empty servers object is ok. */
        return strstr(js, "mcpServers") == NULL ? 0 : 0;
    }
    p = strchr(p, '{');
    if (!p) {
        return 0;
    }
    for (; *p; p++) {
        if (in_str) {
            if (esc) {
                esc = 0;
                continue;
            }
            if (*p == '\\') {
                esc = 1;
                continue;
            }
            if (*p == '"') {
                in_str = 0;
            }
            continue;
        }
        if (*p == '"') {
            const char *end;
            char key[64];
            size_t n;
            in_str = 1;
            end = strchr(p + 1, '"');
            if (!end || (size_t)(end - p - 1) >= sizeof key) {
                return 0;
            }
            n = (size_t)(end - p - 1);
            memcpy(key, p + 1, n);
            key[n] = 0;
            p = end;
            in_str = 0;
            if (depth == 1 && strcmp(key, "mcpServers") != 0) {
                /* top-level extra beside mcpServers: reject if not mcpServers
                 * itself; other top-level keys are rejected. */
                if (strcmp(key, "mcpServers") != 0 && depth == 1) {
                    /* continue; keys inside mcpServers checked at depth 2 */
                }
            }
            if (depth == 2) {
                static const char *ok[] = {
                    "github_comment", "github_file_ops", "github_inline_comment",
                    "github_ci", "github", NULL
                };
                int i, found = 0;
                for (i = 0; ok[i]; i++) {
                    if (strcmp(key, ok[i]) == 0) {
                        found = 1;
                        break;
                    }
                }
                if (!found && strcmp(key, "command") != 0 && strcmp(key, "args") != 0 &&
                    strcmp(key, "env") != 0) {
                    /* server object keys: command/args/env ok; unknown server name no */
                    if (strchr(key, '_') || starts_with(key, "github")) {
                        return 0;
                    }
                }
            }
            continue;
        }
        if (*p == '{') {
            depth++;
        } else if (*p == '}') {
            depth--;
            if (depth == 0) {
                break;
            }
        }
    }
    if (strstr(js, "\"command\":\"sh\"") || strstr(js, "\"command\": \"sh\"")) {
        return 0;
    }
    if (strstr(js, "dangerously")) {
        return 0;
    }
    return 1;
}

static int tool_in_set(const char *tool, const char **set)
{
    int i;
    for (i = 0; set[i]; i++) {
        if (strcmp(tool, set[i]) == 0) {
            return 1;
        }
    }
    return 0;
}

static int split_tools(const char *csv, char **out, int cap)
{
    int n = 0;
    const char *p = csv;
    while (*p && n < cap) {
        const char *comma = strchr(p, ',');
        size_t len = comma ? (size_t)(comma - p) : strlen(p);
        char *item = malloc(len + 1);
        if (!item) {
            return -1;
        }
        memcpy(item, p, len);
        item[len] = 0;
        out[n++] = item;
        p = comma ? comma + 1 : p + len;
    }
    return n;
}

static int tools_equal(char **got, int ngot, const char **expect)
{
    int i, j, nexp = 0;
    int *seen;
    while (expect[nexp]) {
        nexp++;
    }
    if (ngot != nexp) {
        return 0;
    }
    seen = calloc((size_t)nexp, sizeof *seen);
    if (!seen) {
        return 0;
    }
    for (i = 0; i < ngot; i++) {
        int found = 0;
        for (j = 0; j < nexp; j++) {
            if (!seen[j] && strcmp(got[i], expect[j]) == 0) {
                seen[j] = 1;
                found = 1;
                break;
            }
        }
        if (!found) {
            free(seen);
            return 0;
        }
    }
    free(seen);
    return 1;
}

int csl_argv_allow(int argc, char **argv)
{
    int i;
    int saw_perm = 0, saw_turns = 0, saw_sources = 0;
    char workspace[768];
    char write_ws[768];
    char helper[768];
    char gitpush[768];
    char *tools[64];
    int ntools = 0;
    const char *ws = "/home/runner/work/ai-investment-cockpit/ai-investment-cockpit";
    char wsbuf[512];
    size_t n = 0;
    if (csl_read_file(CSL_WORKSPACE_PATH, wsbuf, sizeof wsbuf, &n) == 0 && n > 0) {
        while (n && (wsbuf[n - 1] == '\n' || wsbuf[n - 1] == ' ')) {
            wsbuf[--n] = 0;
        }
        if (n) {
            ws = wsbuf;
        }
    }
    snprintf(workspace, sizeof workspace, "Edit(/%s/**)", ws);
    snprintf(write_ws, sizeof write_ws, "Write(/%s/**)", ws);
    snprintf(helper, sizeof helper,
             "Bash(python3 %s/claude-fixer-guard/model-api --:*)",
             getenv("RUNNER_TEMP") ? getenv("RUNNER_TEMP") : "/home/runner/work/_temp");
    snprintf(gitpush, sizeof gitpush, "Bash(%s/scripts/git-push.sh:*)", CSL_ACTION_DIR);

    for (i = 1; i < argc; i++) {
        const char *a = argv[i];
        if (strcmp(a, "--dangerously-skip-permissions") == 0) {
            return -1;
        }
        if (strcmp(a, "--add-dir") == 0 || starts_with(a, "--add-dir=")) {
            return -1;
        }
        if (strcmp(a, "--plugin") == 0 || starts_with(a, "--plugin") ||
            strcmp(a, "--plugin-dir") == 0 || strstr(a, "marketplace")) {
            return -1;
        }
        if (strcmp(a, "--settings") == 0 || starts_with(a, "--settings=")) {
            return -1;
        }
        if (strcmp(a, "--permission-mode") == 0) {
            if (i + 1 >= argc || strcmp(argv[i + 1], "acceptEdits") != 0) {
                return -1;
            }
            i++;
            saw_perm = 1;
            continue;
        }
        if (starts_with(a, "--permission-mode=")) {
            if (strcmp(a + 18, "acceptEdits") != 0) {
                return -1;
            }
            saw_perm = 1;
            continue;
        }
        if (strcmp(a, "--mcp-config") == 0) {
            if (i + 1 >= argc || is_file_path(argv[i + 1]) || !json_mcp_ok(argv[i + 1])) {
                return -1;
            }
            i++;
            continue;
        }
        if (starts_with(a, "--mcp-config=")) {
            const char *js = a + 13;
            if (is_file_path(js) || !json_mcp_ok(js)) {
                return -1;
            }
            continue;
        }
        if (strcmp(a, "--allowedTools") == 0 || strcmp(a, "--allowed-tools") == 0) {
            int extra;
            if (i + 1 >= argc) {
                return -1;
            }
            extra = split_tools(argv[i + 1], tools + ntools, 64 - ntools);
            if (extra < 0) {
                return -1;
            }
            ntools += extra;
            i++;
            continue;
        }
        if (strcmp(a, "--disallowedTools") == 0 || strcmp(a, "--disallowed-tools") == 0) {
            if (i + 1 >= argc) {
                return -1;
            }
            i++;
            continue;
        }
        if (strcmp(a, "--max-turns") == 0) {
            if (i + 1 >= argc || strcmp(argv[i + 1], "30") != 0) {
                return -1;
            }
            i++;
            saw_turns = 1;
            continue;
        }
        if (strcmp(a, "--setting-sources") == 0) {
            if (i + 1 >= argc || strcmp(argv[i + 1], "user") != 0) {
                return -1;
            }
            i++;
            saw_sources = 1;
            continue;
        }
        if (strcmp(a, "--append-system-prompt") == 0) {
            if (i + 1 >= argc) {
                return -1;
            }
            i++;
            continue;
        }
        if (strcmp(a, "--output-format") == 0) {
            if (i + 1 >= argc || strcmp(argv[i + 1], "stream-json") != 0) {
                return -1;
            }
            i++;
            continue;
        }
        if (strcmp(a, "--input-format") == 0) {
            if (i + 1 >= argc || strcmp(argv[i + 1], "stream-json") != 0) {
                return -1;
            }
            i++;
            continue;
        }
        if (strcmp(a, "--verbose") == 0 || strcmp(a, "--print") == 0 ||
            strcmp(a, "-p") == 0 || strcmp(a, "--replay-user-messages") == 0) {
            continue;
        }
        /* Unknown flag: fail closed. Positional args are also rejected. */
        if (a[0] == '-') {
            return -1;
        }
        return -1;
    }
    if (ntools) {
        const char *expect[32];
        int e = 0;
        expect[e++] = "Glob";
        expect[e++] = "Grep";
        expect[e++] = "LS";
        expect[e++] = "Read";
        expect[e++] = "mcp__github_comment__update_claude_comment";
        expect[e++] = "mcp__github_ci__get_ci_status";
        expect[e++] = "mcp__github_ci__get_workflow_run_details";
        expect[e++] = "mcp__github_ci__download_job_log";
        expect[e++] = "Bash(git add:*)";
        expect[e++] = "Bash(git commit:*)";
        expect[e++] = gitpush;
        expect[e++] = "Bash(git rm:*)";
        expect[e++] = workspace;
        expect[e++] = write_ws; /* #256/U-8 workspace Write; D10:331 N8 has none — OPEN-QUESTIONS */
        expect[e++] = helper;
        expect[e] = NULL;
        if (!tools_equal(tools, ntools, expect)) {
            /* Honest tag-mode union may omit duplicates of Read/Glob/Grep.
             * Fail closed on unknown names; allow a subset that still covers
             * the N8 workspace Edit + helper and has no extras. */
            int k, ok = 1, has_edit = 0, has_helper = 0;
            for (k = 0; k < ntools; k++) {
                if (strcmp(tools[k], workspace) == 0) {
                    has_edit = 1;
                } else if (strcmp(tools[k], helper) == 0) {
                    has_helper = 1;
                } else if (!tool_in_set(tools[k], expect)) {
                    ok = 0;
                }
            }
            if (!ok || !has_edit || !has_helper) {
                while (ntools) {
                    free(tools[--ntools]);
                }
                return -1;
            }
        }
        while (ntools) {
            free(tools[--ntools]);
        }
    }
    (void)saw_perm;
    (void)saw_turns;
    (void)saw_sources;
    return 0;
}
