/* CSL-474 D10 shared helpers. Statically linked. No env path overrides. */
#include "sandbox_common.h"

#include <ctype.h>
#include <dirent.h>
#include <limits.h>
#include <sys/syscall.h>

void csl_die(int code, const char *reason)
{
    /* Non-secret reason only. Never log argv or tokens. */
    const char *p = reason ? reason : "error";
    if (write(2, p, strlen(p)) < 0) {
        /* ignore */
    }
    if (write(2, "\n", 1) < 0) {
        /* ignore */
    }
    _exit(code);
}

int csl_write_all(int fd, const void *buf, size_t n)
{
    const unsigned char *p = buf;
    size_t off = 0;
    while (off < n) {
        ssize_t w = write(fd, p + off, n - off);
        if (w < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        if (w == 0) {
            return -1;
        }
        off += (size_t)w;
    }
    return 0;
}

int csl_read_all(int fd, unsigned char *buf, size_t cap, size_t *out)
{
    size_t off = 0;
    while (off < cap) {
        ssize_t r = read(fd, buf + off, cap - off);
        if (r < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        if (r == 0) {
            break;
        }
        off += (size_t)r;
    }
    if (out) {
        *out = off;
    }
    return 0;
}

static const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
};

static uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

static void sha256_transform(csl_sha256_ctx *ctx, const unsigned char data[64])
{
    uint32_t m[64], a, b, c, d, e, f, g, h, t1, t2;
    int i;
    for (i = 0; i < 16; i++) {
        m[i] = ((uint32_t)data[i * 4] << 24) | ((uint32_t)data[i * 4 + 1] << 16) |
               ((uint32_t)data[i * 4 + 2] << 8) | ((uint32_t)data[i * 4 + 3]);
    }
    for (; i < 64; i++) {
        uint32_t s0 = rotr(m[i - 15], 7) ^ rotr(m[i - 15], 18) ^ (m[i - 15] >> 3);
        uint32_t s1 = rotr(m[i - 2], 17) ^ rotr(m[i - 2], 19) ^ (m[i - 2] >> 10);
        m[i] = m[i - 16] + s0 + m[i - 7] + s1;
    }
    a = ctx->state[0];
    b = ctx->state[1];
    c = ctx->state[2];
    d = ctx->state[3];
    e = ctx->state[4];
    f = ctx->state[5];
    g = ctx->state[6];
    h = ctx->state[7];
    for (i = 0; i < 64; i++) {
        uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        uint32_t ch = (e & f) ^ ((~e) & g);
        t1 = h + S1 + ch + K[i] + m[i];
        uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        t2 = S0 + maj;
        h = g;
        g = f;
        f = e;
        e = d + t1;
        d = c;
        c = b;
        b = a;
        a = t1 + t2;
    }
    ctx->state[0] += a;
    ctx->state[1] += b;
    ctx->state[2] += c;
    ctx->state[3] += d;
    ctx->state[4] += e;
    ctx->state[5] += f;
    ctx->state[6] += g;
    ctx->state[7] += h;
}

void csl_sha256_init(csl_sha256_ctx *ctx)
{
    ctx->bitcount = 0;
    ctx->state[0] = 0x6a09e667;
    ctx->state[1] = 0xbb67ae85;
    ctx->state[2] = 0x3c6ef372;
    ctx->state[3] = 0xa54ff53a;
    ctx->state[4] = 0x510e527f;
    ctx->state[5] = 0x9b05688c;
    ctx->state[6] = 0x1f83d9ab;
    ctx->state[7] = 0x5be0cd19;
    memset(ctx->buffer, 0, sizeof ctx->buffer);
}

void csl_sha256_update(csl_sha256_ctx *ctx, const void *data, size_t len)
{
    const unsigned char *p = data;
    size_t i;
    for (i = 0; i < len; i++) {
        ctx->buffer[ctx->bitcount / 8 % 64] = p[i];
        ctx->bitcount += 8;
        if ((ctx->bitcount / 8) % 64 == 0) {
            sha256_transform(ctx, ctx->buffer);
        }
    }
}

void csl_sha256_final(csl_sha256_ctx *ctx, unsigned char out[32])
{
    unsigned char pad[64];
    uint64_t bits = ctx->bitcount;
    size_t i;
    pad[0] = 0x80;
    memset(pad + 1, 0, 63);
    size_t rem = (size_t)((ctx->bitcount / 8) % 64);
    size_t padlen = (rem < 56) ? (56 - rem) : (120 - rem);
    csl_sha256_update(ctx, pad, padlen);
    for (i = 0; i < 8; i++) {
        pad[i] = (unsigned char)(bits >> (56 - 8 * i));
    }
    csl_sha256_update(ctx, pad, 8);
    for (i = 0; i < 8; i++) {
        out[i * 4] = (unsigned char)(ctx->state[i] >> 24);
        out[i * 4 + 1] = (unsigned char)(ctx->state[i] >> 16);
        out[i * 4 + 2] = (unsigned char)(ctx->state[i] >> 8);
        out[i * 4 + 3] = (unsigned char)ctx->state[i];
    }
}

int csl_sha256_fd(int fd, unsigned char out[32])
{
    csl_sha256_ctx ctx;
    unsigned char buf[8192];
    csl_sha256_init(&ctx);
    if (lseek(fd, 0, SEEK_SET) < 0 && errno != ESPIPE) {
        return -1;
    }
    for (;;) {
        ssize_t r = read(fd, buf, sizeof buf);
        if (r < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        if (r == 0) {
            break;
        }
        csl_sha256_update(&ctx, buf, (size_t)r);
    }
    csl_sha256_final(&ctx, out);
    if (lseek(fd, 0, SEEK_SET) < 0 && errno != ESPIPE) {
        return -1;
    }
    return 0;
}

int csl_sha256_file(const char *path, unsigned char out[32])
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    int rc;
    if (fd < 0) {
        return -1;
    }
    rc = csl_sha256_fd(fd, out);
    close(fd);
    return rc;
}

void csl_hex32(const unsigned char in[32], char out[65])
{
    static const char *hex = "0123456789abcdef";
    int i;
    for (i = 0; i < 32; i++) {
        out[i * 2] = hex[in[i] >> 4];
        out[i * 2 + 1] = hex[in[i] & 0xf];
    }
    out[64] = 0;
}

int csl_hex_eq(const unsigned char digest[32], const char *hex64)
{
    char got[65];
    if (!hex64 || strlen(hex64) != 64) {
        return 0;
    }
    csl_hex32(digest, got);
    return strcmp(got, hex64) == 0;
}

int csl_lookup_user(uid_t uid, char *name, size_t nlen, char *home, size_t hlen)
{
    FILE *f = fopen("/etc/passwd", "r");
    char line[1024];
    if (!f) {
        return -1;
    }
    while (fgets(line, sizeof line, f)) {
        char *fields[7];
        int n = 0;
        char *p = line;
        char *nl = strchr(line, '\n');
        if (nl) {
            *nl = 0;
        }
        fields[n++] = p;
        for (; *p && n < 7; p++) {
            if (*p == ':') {
                *p = 0;
                fields[n++] = p + 1;
            }
        }
        if (n < 6) {
            continue;
        }
        if ((uid_t)atoi(fields[2]) == uid) {
            snprintf(name, nlen, "%s", fields[0]);
            snprintf(home, hlen, "%s", fields[5]);
            fclose(f);
            return 0;
        }
    }
    fclose(f);
    return -1;
}

pid_t csl_proc_ppid(pid_t pid)
{
    char path[64], buf[4096];
    int fd;
    char *rparen;
    char state = 0;
    int pp = 0;
    snprintf(path, sizeof path, "/proc/%d/stat", (int)pid);
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        return 0;
    }
    if (read(fd, buf, sizeof buf - 1) <= 0) {
        close(fd);
        return 0;
    }
    close(fd);
    rparen = strrchr(buf, ')');
    if (!rparen || sscanf(rparen + 2, "%c %d", &state, &pp) != 2) {
        return 0;
    }
    return (pid_t)pp;
}

int csl_lookup_name(const char *name, uid_t *uid)
{
    FILE *f;
    char line[1024];
    if (!name || !uid) {
        return -1;
    }
    f = fopen("/etc/passwd", "r");
    if (!f) {
        return -1;
    }
    while (fgets(line, sizeof line, f)) {
        char *fields[7];
        int n = 0;
        char *p = line;
        char *nl = strchr(line, '\n');
        if (nl) {
            *nl = 0;
        }
        fields[n++] = p;
        for (; *p && n < 7; p++) {
            if (*p == ':') {
                *p = 0;
                fields[n++] = p + 1;
            }
        }
        if (n < 3) {
            continue;
        }
        if (strcmp(fields[0], name) == 0) {
            *uid = (uid_t)atoi(fields[2]);
            fclose(f);
            return 0;
        }
    }
    fclose(f);
    return -1;
}

static int e3_token_ok(const char *s)
{
    const unsigned char *p;
    if (!s) {
        return 0;
    }
    for (p = (const unsigned char *)s; *p; p++) {
        if (*p < 0x20 || *p == 0x7f || *p == ' ' || *p == '\t') {
            return 0;
        }
    }
    return 1;
}

int csl_e3_record_ok(const char *text, size_t n)
{
    static const char *ok[] = {
        "claude_sha256", "claude_realpath_class", "sdk_mjs_sha256", "sdk_source",
        "sdk_disk_match", "bun_tofu_sha256", "bwrap_sha256", "socat_sha256",
        "bash_sha256", "match", "handoff_count", "handoff_conflict", NULL
    };
    char buf[4096];
    char *line, *save = NULL;
    int saw = 0, need = 0, i;
    unsigned present = 0;
    while (ok[need]) {
        need++;
    }
    if (n == 0 || n >= sizeof buf) {
        return 0;
    }
    if (memchr(text, 0, n)) {
        return 0;
    }
    memcpy(buf, text, n);
    buf[n] = 0;
    for (line = strtok_r(buf, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char *eq = strchr(line, '=');
        size_t klen;
        int found = 0;
        if (!eq || eq == line) {
            return 0;
        }
        *eq = 0;
        if (!e3_token_ok(line) || !e3_token_ok(eq + 1)) {
            return 0;
        }
        klen = strlen(line);
        for (i = 0; ok[i]; i++) {
            if (strlen(ok[i]) == klen && memcmp(line, ok[i], klen) == 0) {
                if (present & (1u << i)) {
                    return 0;
                }
                present |= (1u << i);
                found = 1;
                break;
            }
        }
        if (!found) {
            return 0;
        }
        saw++;
    }
    return saw == need;
}

int csl_e3_set_handoff(char *text, size_t cap, size_t *n, unsigned count, int conflict)
{
    char src[4096], out[4096];
    char *line, *save = NULL;
    size_t used = 0;
    int saw_count = 0, saw_conflict = 0;

    if (!text || !n || *n == 0 || *n >= sizeof src || cap < 2) {
        return -1;
    }
    if (!csl_e3_record_ok(text, *n)) {
        return -1;
    }
    memcpy(src, text, *n);
    src[*n] = 0;
    for (line = strtok_r(src, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char *eq = strchr(line, '=');
        const char *val;
        char tmp[32];
        int wrote;

        if (!eq || eq == line) {
            return -1;
        }
        *eq = 0;
        if (strcmp(line, "handoff_count") == 0) {
            snprintf(tmp, sizeof tmp, "%u", count);
            val = tmp;
            saw_count = 1;
        } else if (strcmp(line, "handoff_conflict") == 0) {
            val = conflict ? "true" : "false";
            saw_conflict = 1;
        } else {
            val = eq + 1;
        }
        wrote = snprintf(out + used, sizeof out - used, "%s=%s\n", line, val);
        if (wrote < 0 || (size_t)wrote >= sizeof out - used) {
            return -1;
        }
        used += (size_t)wrote;
    }
    if (!saw_count || !saw_conflict || used == 0 || used >= cap) {
        return -1;
    }
    memcpy(text, out, used);
    text[used] = 0;
    *n = used;
    if (!csl_e3_record_ok(text, *n)) {
        return -1;
    }
    return 0;
}

int csl_pidfd_open(pid_t pid, unsigned int flags)
{
    return (int)syscall(__NR_pidfd_open, pid, flags);
}

int csl_read_pidfd_pid(int pidfd, pid_t *out)
{
    char path[64], buf[4096], *p;
    int fd;
    ssize_t n;
    snprintf(path, sizeof path, "/proc/self/fdinfo/%d", pidfd);
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        return -1;
    }
    n = read(fd, buf, sizeof buf - 1);
    close(fd);
    if (n <= 0) {
        return -1;
    }
    buf[n] = 0;
    p = strstr(buf, "Pid:");
    if (!p) {
        p = strstr(buf, "pid:");
    }
    if (!p) {
        return -1;
    }
    p += 4;
    while (*p == ' ' || *p == '\t') {
        p++;
    }
    *out = (pid_t)atoi(p);
    return (*out > 0) ? 0 : -1;
}

int csl_proc_exe(pid_t pid, char *buf, size_t len)
{
    char path[64];
    ssize_t n;
    snprintf(path, sizeof path, "/proc/%d/exe", (int)pid);
    n = readlink(path, buf, len - 1);
    if (n < 0) {
        return -1;
    }
    buf[n] = 0;
    return 0;
}

int csl_proc_cmdline(pid_t pid, char *buf, size_t len, size_t *nuls)
{
    char path[64];
    int fd;
    ssize_t n;
    snprintf(path, sizeof path, "/proc/%d/cmdline", (int)pid);
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        return -1;
    }
    n = read(fd, buf, len - 1);
    close(fd);
    if (n < 0) {
        return -1;
    }
    buf[n] = 0;
    if (nuls) {
        *nuls = (size_t)n;
    }
    return 0;
}

int csl_mkdir_p(const char *path, mode_t mode)
{
    char tmp[PATH_MAX];
    size_t i, n;
    if (!path || path[0] != '/') {
        return -1;
    }
    n = strlen(path);
    if (n >= sizeof tmp) {
        return -1;
    }
    memcpy(tmp, path, n + 1);
    for (i = 1; i < n; i++) {
        if (tmp[i] == '/') {
            tmp[i] = 0;
            if (mkdir(tmp, mode) < 0 && errno != EEXIST) {
                return -1;
            }
            tmp[i] = '/';
        }
    }
    if (mkdir(tmp, mode) < 0 && errno != EEXIST) {
        return -1;
    }
    return 0;
}

int csl_write_file(const char *path, const void *data, size_t n, mode_t mode)
{
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, mode);
    if (fd < 0) {
        return -1;
    }
    if (csl_write_all(fd, data, n) < 0) {
        close(fd);
        return -1;
    }
    if (fchmod(fd, mode) < 0) {
        close(fd);
        return -1;
    }
    close(fd);
    return 0;
}

int csl_read_file(const char *path, char *buf, size_t cap, size_t *n)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    size_t got = 0;
    if (fd < 0) {
        return -1;
    }
    if (csl_read_all(fd, (unsigned char *)buf, cap - 1, &got) < 0) {
        close(fd);
        return -1;
    }
    close(fd);
    buf[got] = 0;
    if (n) {
        *n = got;
    }
    return 0;
}

int csl_path_root_owned(const char *path)
{
    struct stat st;
    if (lstat(path, &st) < 0) {
        return 0;
    }
    if (st.st_uid != 0) {
        return 0;
    }
    if (st.st_mode & (S_IWGRP | S_IWOTH)) {
        return 0;
    }
    return 1;
}

int csl_ancestors_root_owned(const char *path)
{
    char tmp[PATH_MAX];
    size_t n;
    if (!path || path[0] != '/') {
        return 0;
    }
    n = strlen(path);
    if (n >= sizeof tmp) {
        return 0;
    }
    memcpy(tmp, path, n + 1);
    while (n > 1) {
        char *slash;
        if (!csl_path_root_owned(tmp)) {
            return 0;
        }
        slash = strrchr(tmp, '/');
        if (!slash) {
            break;
        }
        if (slash == tmp) {
            tmp[1] = 0;
            return csl_path_root_owned(tmp);
        }
        *slash = 0;
        n = strlen(tmp);
    }
    return 1;
}

int csl_truthy(const char *value)
{
    if (!value || !value[0]) {
        return 0;
    }
    return strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
           strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0;
}

int csl_env_present(const char *name)
{
    return getenv(name) != NULL;
}

const char *csl_env_get(const char *name)
{
    const char *v = getenv(name);
    return v ? v : "";
}
