/* D10 E3-2 argv allowlist. Never log argv. Rejection is bad_argv only.
 * Exact workflow set plus injected launcher-owned MCP (N1/N6). */
#include "sandbox_argv.h"

static int starts_with(const char *s, const char *p)
{
    return strncmp(s, p, strlen(p)) == 0;
}

/* SDK 0.3.306 emits Ke("--flag", v) as a single "--flag=v" token for
 * max-turns, allowedTools, disallowedTools, and setting-sources. YAML
 * two-token form is still accepted with the same value checks. */
static int take_eq_or_next(const char *a, const char *flag, int *i, int argc,
                           char **argv, const char **value)
{
    size_t flen = strlen(flag);
    if (strcmp(a, flag) == 0) {
        if (*i + 1 >= argc) {
            return -1;
        }
        (*i)++;
        *value = argv[*i];
        return 1;
    }
    if (starts_with(a, flag) && a[flen] == '=') {
        *value = a + flen + 1;
        return 1;
    }
    return 0;
}

/* SDK my() never emits a separate value token that starts with '-'.
 * Empty values are also refuse (fail closed). */
static int value_ok(const char *v)
{
    return v && v[0] != '\0' && v[0] != '-';
}

#define CSL_ARGV_MAX 64

static void mark_drop(unsigned char *drop, int i)
{
    if (drop && i >= 0 && i < CSL_ARGV_MAX) {
        drop[i] = 1;
    }
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

static int classify(int argc, char **argv, unsigned char *drop)
{
    int i;
    int saw_turns = 0, saw_sources = 0, saw_allowed = 0;
    int saw_disallowed = 0, saw_append = 0, saw_perm = 0;
    char workspace[768];
    char write_ws[768];
    char helper[768];
    char *tools[64];
    int ntools = 0;
    const char *ws = CSL_WORKSPACE_DEFAULT;
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

    if (argc < 1 || argc >= CSL_ARGV_MAX) {
        return -1;
    }
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
            if (i + 1 >= argc || !value_ok(argv[i + 1])) {
                free_tools(tools, ntools);
                return -1;
            }
            mark_drop(drop, i);
            mark_drop(drop, i + 1);
            i++;
            continue;
        }
        if (starts_with(a, "--mcp-config=")) {
            if (!value_ok(a + 13)) {
                free_tools(tools, ntools);
                return -1;
            }
            mark_drop(drop, i);
            continue;
        }
        if (strcmp(a, "--strict-mcp-config") == 0) {
            mark_drop(drop, i);
            continue;
        }
        if (strcmp(a, "--permission-mode") == 0) {
            if (saw_perm || i + 1 >= argc || !value_ok(argv[i + 1]) ||
                strcmp(argv[i + 1], "acceptEdits") != 0) {
                free_tools(tools, ntools);
                return -1;
            }
            i++;
            saw_perm = 1;
            continue;
        }
        if (starts_with(a, "--permission-mode=")) {
            if (saw_perm || !value_ok(a + 18) || strcmp(a + 18, "acceptEdits") != 0) {
                free_tools(tools, ntools);
                return -1;
            }
            saw_perm = 1;
            continue;
        }
        {
            const char *val = NULL;
            int t = take_eq_or_next(a, "--max-turns", &i, argc, argv, &val);
            if (t < 0) {
                free_tools(tools, ntools);
                return -1;
            }
            if (t > 0) {
                if (saw_turns || !value_ok(val) || strcmp(val, "30") != 0) {
                    free_tools(tools, ntools);
                    return -1;
                }
                saw_turns = 1;
                continue;
            }
        }
        {
            const char *val = NULL;
            int t = take_eq_or_next(a, "--setting-sources", &i, argc, argv, &val);
            if (t < 0) {
                free_tools(tools, ntools);
                return -1;
            }
            if (t > 0) {
                if (saw_sources || !value_ok(val) || strcmp(val, "user") != 0) {
                    free_tools(tools, ntools);
                    return -1;
                }
                saw_sources = 1;
                continue;
            }
        }
        {
            const char *val = NULL;
            int t = take_eq_or_next(a, "--allowedTools", &i, argc, argv, &val);
            if (t == 0) {
                t = take_eq_or_next(a, "--allowed-tools", &i, argc, argv, &val);
            }
            if (t < 0) {
                free_tools(tools, ntools);
                return -1;
            }
            if (t > 0) {
                int extra;
                if (saw_allowed || !value_ok(val)) {
                    free_tools(tools, ntools);
                    return -1;
                }
                extra = split_tools(val, tools + ntools, 64 - ntools);
                if (extra < 0) {
                    free_tools(tools, ntools);
                    return -1;
                }
                ntools += extra;
                saw_allowed = 1;
                continue;
            }
        }
        {
            const char *val = NULL;
            int t = take_eq_or_next(a, "--disallowedTools", &i, argc, argv, &val);
            if (t == 0) {
                t = take_eq_or_next(a, "--disallowed-tools", &i, argc, argv, &val);
            }
            if (t < 0) {
                free_tools(tools, ntools);
                return -1;
            }
            if (t > 0) {
                if (saw_disallowed || !value_ok(val)) {
                    free_tools(tools, ntools);
                    return -1;
                }
                saw_disallowed = 1;
                continue;
            }
        }
        if (strcmp(a, "--append-system-prompt") == 0) {
            if (saw_append || i + 1 >= argc || !value_ok(argv[i + 1])) {
                free_tools(tools, ntools);
                return -1;
            }
            i++;
            saw_append = 1;
            continue;
        }
        if (strcmp(a, "--output-format") == 0) {
            if (i + 1 >= argc || !value_ok(argv[i + 1]) ||
                strcmp(argv[i + 1], "stream-json") != 0) {
                free_tools(tools, ntools);
                return -1;
            }
            i++;
            continue;
        }
        if (strcmp(a, "--input-format") == 0) {
            if (i + 1 >= argc || !value_ok(argv[i + 1]) ||
                strcmp(argv[i + 1], "stream-json") != 0) {
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

int csl_argv_allow(int argc, char **argv)
{
    return classify(argc, argv, NULL);
}

int csl_argv_rewrite(int argc, char **argv, char **out, int cap, int *outn)
{
    unsigned char drop[CSL_ARGV_MAX];
    int i, n = 0;
    if (cap < 4 || !argv || !out || argc < 1 || argc >= CSL_ARGV_MAX) {
        return -1;
    }
    memset(drop, 0, sizeof drop);
    if (classify(argc, argv, drop) != 0) {
        return -1;
    }
    out[n++] = argv[0];
    for (i = 1; i < argc; i++) {
        if (drop[i]) {
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
