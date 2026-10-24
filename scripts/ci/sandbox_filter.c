/* D10 §2.3 reconstruct-never-copy filter. Grammar compiled in. */
#include "sandbox_filter.h"

static const char *TOKEN_ORDER[] = {
    "network", "filesystem", "env", "seccomp(unix-block)"
};
static const char MATCH_PREFIX[] =
    "[SandboxDebug] [Sandbox Linux] Wrapped command with bwrap (";
static const char MATCH_SUFFIX[] = " restrictions)";
static const char RE_PREFIX[] = "[Sandbox Linux] Wrapped command with bwrap (";
static const char RE_SUFFIX[] = " restrictions)";

void csl_reconstruct(unsigned tokens, char *out, size_t cap)
{
    char inner[128];
    size_t i, o = 0;
    inner[0] = 0;
    for (i = 0; i < 4; i++) {
        if (tokens & (1u << i)) {
            if (o) {
                memcpy(inner + o, ", ", 2);
                o += 2;
            }
            size_t n = strlen(TOKEN_ORDER[i]);
            memcpy(inner + o, TOKEN_ORDER[i], n);
            o += n;
        }
    }
    inner[o] = 0;
    snprintf(out, cap, "%s%s%s", RE_PREFIX, inner, RE_SUFFIX);
}

static int parse_tokens(const char *line, unsigned *out)
{
    size_t plen = sizeof MATCH_PREFIX - 1;
    size_t slen = sizeof MATCH_SUFFIX - 1;
    size_t n = strlen(line);
    const char *inner;
    size_t ilen;
    unsigned bits = 0, last = 0, seen_any = 0;
    if (n < plen + slen) {
        return 0;
    }
    if (memcmp(line, MATCH_PREFIX, plen) != 0) {
        return 0;
    }
    if (memcmp(line + n - slen, MATCH_SUFFIX, slen) != 0) {
        return 0;
    }
    inner = line + plen;
    ilen = n - plen - slen;
    if (ilen == 0) {
        *out = 0;
        return 1;
    }
    while (ilen) {
        unsigned i;
        int found = 0;
        if (seen_any) {
            if (ilen < 2 || inner[0] != ',' || inner[1] != ' ') {
                return 0;
            }
            inner += 2;
            ilen -= 2;
        }
        for (i = last; i < 4; i++) {
            size_t tn = strlen(TOKEN_ORDER[i]);
            if (ilen >= tn && memcmp(inner, TOKEN_ORDER[i], tn) == 0) {
                bits |= 1u << i;
                inner += tn;
                ilen -= tn;
                last = i + 1;
                found = 1;
                seen_any = 1;
                break;
            }
        }
        if (!found) {
            return 0;
        }
    }
    *out = bits;
    return 1;
}

csl_filter_result csl_filter_capture(const unsigned char *data, size_t n)
{
    unsigned char *buf;
    size_t cap = CSL_CAP_BYTES, i, start;
    unsigned agreed = 0;
    int have = 0, conflict = 0, matches = 0;
    csl_filter_result r;
    memset(&r, 0, sizeof r);
    buf = calloc(1, cap);
    if (!buf) {
        r.status = CSL_STATUS_NOT_OBSERVED;
        return r;
    }
    if (n > cap) {
        n = cap;
    }
    memcpy(buf, data, n);
    /* Trailing NULs are padding, not line content (L-c). */
    start = 0;
    for (i = 0; i <= cap; i++) {
        int at_end = (i == cap);
        int is_nl = (!at_end && buf[i] == '\n');
        int is_nul_run = (!at_end && buf[i] == 0 && i >= n);
        if (!(is_nl || at_end || is_nul_run)) {
            continue;
        }
        size_t ln = i - start;
        /* Stop the line at the first embedded NUL (padding or garbage). */
        size_t k;
        for (k = 0; k < ln; k++) {
            if (buf[start + k] == 0) {
                ln = k;
                break;
            }
        }
        if (ln > 0) {
            if (ln <= CSL_MAX_LINE && memchr(buf + start, '\r', ln) == NULL) {
                char line[CSL_MAX_LINE + 1];
                unsigned toks = 0;
                memcpy(line, buf + start, ln);
                line[ln] = 0;
                if (parse_tokens(line, &toks)) {
                    matches++;
                    if (!have) {
                        agreed = toks;
                        have = 1;
                    } else if (agreed != toks) {
                        conflict = 1;
                    }
                }
            }
        }
        if (is_nul_run) {
            break;
        }
        start = i + 1;
        if (at_end) {
            break;
        }
    }
    free(buf);
    if (conflict) {
        r.status = CSL_STATUS_CONFLICT;
        return r;
    }
    if (!have || matches == 0) {
        r.status = CSL_STATUS_NOT_OBSERVED;
        return r;
    }
    r.status = CSL_STATUS_OBSERVED;
    r.tokens = agreed;
    r.positive = agreed != 0;
    return r;
}
