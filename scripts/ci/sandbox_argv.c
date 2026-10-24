/* D10 E3-2 argv allowlist. Never log argv. Rejection is bad_argv only.
 * Exact workflow set plus injected launcher-owned MCP (N1/N6). */
#include "sandbox_argv.h"

static int starts_with(const char *s, const char *p)
{
    return strncmp(s, p, strlen(p)) == 0;
}

static void free_tools(char **tools, int n)
{
    while (n > 0) {
        free(tools[--n]);
    }
}

static int split_tools(const char *csv, char **out, int cap)
{
    int n = 0;
    const char *p = csv;
    if (!csv) {
        return -1;
    }
    while (*p && n < cap) {
        const char *comma = strchr(p, ',');
        size_t len = comma ? (size_t)(comma - p) : strlen(p);
        char *item = malloc(len + 1);
        if (!item) {
            free_tools(out, n);
            return -1;
        }
        memcpy(item, p, len);
        item[len] = 0;
        out[n++] = item;
        p = comma ? comma + 1 : p + len;
    }
    if (*p) {
        free_tools(out, n);
        return -1;
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
    int saw_turns = 0, saw_sources = 0, saw_allowed = 0;
    int saw_disallowed = 0, saw_append = 0, saw_perm = 0;
    char workspace[768];
    char write_ws[768];
    char helper[768];
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
             "Bash(python3 %s/claude-fixer-guard/model-api --:*)", CSL_RUNNER_TEMP);

    for (i = 1; i < argc; i++) {
        const char *a = argv[i];
        if (strcmp(a, "--dangerously-skip-permissions") == 0) {
            free_tools(tools, ntools);
            return -1;
        }
        if (strcmp(a, "--add-dir") == 0 || starts_with(a, "--add-dir=")) {
            free_tools(tools, ntools);
            return -1;
        }
        if (strcmp(a, "--plugin") == 0 || starts_with(a, "--plugin") ||
            strcmp(a, "--plugin-dir") == 0 || strstr(a, "marketplace")) {
            free_tools(tools, ntools);
            return -1;
        }
        if (strcmp(a, "--settings") == 0 || starts_with(a, "--settings=")) {
            free_tools(tools, ntools);
            return -1;
        }
        /* Incoming MCP is discarded; launcher injects its own empty file. */
        if (strcmp(a, "--mcp-config") == 0) {
            if (i + 1 >= argc) {
                free_tools(tools, ntools);
                return -1;
            }
            i++;
            continue;
        }
        if (starts_with(a, "--mcp-config=")) {
            continue;
        }
        if (strcmp(a, "--strict-mcp-config") == 0) {
            continue;
        }
        if (strcmp(a, "--permission-mode") == 0) {
            if (saw_perm || i + 1 >= argc || strcmp(argv[i + 1], "acceptEdits") != 0) {
                free_tools(tools, ntools);
                return -1;
            }
            i++;
            saw_perm = 1;
            continue;
        }
        if (starts_with(a, "--permission-mode=")) {
            if (saw_perm || strcmp(a + 18, "acceptEdits") != 0) {
                free_tools(tools, ntools);
                return -1;
            }
            saw_perm = 1;
            continue;
        }
        if (strcmp(a, "--max-turns") == 0) {
            if (saw_turns || i + 1 >= argc || strcmp(argv[i + 1], "30") != 0) {
                free_tools(tools, ntools);
                return -1;
            }
            i++;
            saw_turns = 1;
            continue;
        }
        if (strcmp(a, "--setting-sources") == 0) {
            if (saw_sources || i + 1 >= argc || strcmp(argv[i + 1], "user") != 0) {
                free_tools(tools, ntools);
                return -1;
            }
            i++;
            saw_sources = 1;
            continue;
        }
        if (strcmp(a, "--allowedTools") == 0 || strcmp(a, "--allowed-tools") == 0) {
            int extra;
            if (saw_allowed || i + 1 >= argc) {
                free_tools(tools, ntools);
                return -1;
            }
            extra = split_tools(argv[i + 1], tools + ntools, 64 - ntools);
            if (extra < 0) {
                return -1;
            }
            ntools += extra;
            saw_allowed = 1;
            i++;
            continue;
        }
        if (strcmp(a, "--disallowedTools") == 0 || strcmp(a, "--disallowed-tools") == 0) {
            if (saw_disallowed || i + 1 >= argc) {
                free_tools(tools, ntools);
                return -1;
            }
            i++;
            saw_disallowed = 1;
            continue;
        }
        if (strcmp(a, "--append-system-prompt") == 0) {
            if (saw_append || i + 1 >= argc) {
                free_tools(tools, ntools);
                return -1;
            }
            i++;
            saw_append = 1;
            continue;
        }
        if (strcmp(a, "--output-format") == 0) {
            if (i + 1 >= argc || strcmp(argv[i + 1], "stream-json") != 0) {
                free_tools(tools, ntools);
                return -1;
            }
            i++;
            continue;
        }
        if (strcmp(a, "--input-format") == 0) {
            if (i + 1 >= argc || strcmp(argv[i + 1], "stream-json") != 0) {
                free_tools(tools, ntools);
                return -1;
            }
            i++;
            continue;
        }
        if (strcmp(a, "--verbose") == 0 || strcmp(a, "--print") == 0 ||
            strcmp(a, "-p") == 0 || strcmp(a, "--replay-user-messages") == 0) {
            continue;
        }
        free_tools(tools, ntools);
        return -1;
    }
    if (!saw_turns || !saw_sources || !saw_allowed || ntools == 0) {
        free_tools(tools, ntools);
        return -1;
    }
    {
        const char *expect[8];
        int e = 0;
        expect[e++] = workspace;
        expect[e++] = write_ws; /* #256 workspace Write; D10:331 N8 has none */
        expect[e++] = "Read";
        expect[e++] = "Glob";
        expect[e++] = "Grep";
        expect[e++] = helper;
        expect[e] = NULL;
        if (!tools_equal(tools, ntools, expect)) {
            free_tools(tools, ntools);
            return -1;
        }
    }
    free_tools(tools, ntools);
    return 0;
}

int csl_argv_rewrite(int argc, char **argv, char **out, int cap, int *outn)
{
    int i, n = 0;
    if (cap < 4 || !argv || !out) {
        return -1;
    }
    out[n++] = argv[0];
    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--mcp-config") == 0) {
            if (i + 1 >= argc) {
                return -1;
            }
            i++;
            continue;
        }
        if (starts_with(argv[i], "--mcp-config=")) {
            continue;
        }
        if (strcmp(argv[i], "--strict-mcp-config") == 0) {
            continue;
        }
        if (n + 4 > cap) {
            return -1;
        }
        out[n++] = argv[i];
    }
    if (n + 4 > cap) {
        return -1;
    }
    out[n++] = "--strict-mcp-config";
    out[n++] = "--mcp-config";
    out[n++] = (char *)CSL_MCP_EMPTY_PATH;
    out[n] = NULL;
    if (outn) {
        *outn = n;
    }
    return 0;
}
