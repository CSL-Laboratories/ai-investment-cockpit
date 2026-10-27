/* D10 §3.8 static relay. Ignore argv. One-shot gated. Only sudoers command. */
#include "sandbox_filter.h"

#include <limits.h>
#include <sys/prctl.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>

static const char LINE_NOT_OBSERVED[] = "[csl-relay] sandbox literal NOT observed";
static const char LINE_CONFLICT[] = "[csl-relay] sandbox literal CONFLICT";
static const char LINE_FILTER_ERROR[] = "[csl-relay] filter error";
static const char LINE_NOT_RELAYED[] = "[csl-relay] NOT RELAYED reason=";

static void token_hex(char out[33])
{
    unsigned char b[16];
    static const char *hex = "0123456789abcdef";
    int i;
    if (getrandom(b, sizeof b, 0) != (ssize_t)sizeof b) {
        int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
        if (fd < 0 || csl_read_all(fd, b, sizeof b, NULL) < 0) {
            csl_die(1, "random");
        }
        if (fd >= 0) {
            close(fd);
        }
    }
    for (i = 0; i < 16; i++) {
        out[i * 2] = hex[b[i] >> 4];
        out[i * 2 + 1] = hex[b[i] & 0xf];
    }
    out[32] = 0;
}

static void wrap_print(const char *body)
{
    char tok[33];
    token_hex(tok);
    fputs("::stop-commands::", stdout);
    fputs(tok, stdout);
    fputc('\n', stdout);
    fputs(body, stdout);
    if (body[0] && body[strlen(body) - 1] != '\n') {
        fputc('\n', stdout);
    }
    fputc(':', stdout);
    fputc(':', stdout);
    fputs(tok, stdout);
    fputs("::\n", stdout);
    fflush(stdout);
}

static void not_relayed(const char *reason)
{
    char buf[160];
    snprintf(buf, sizeof buf, "%s%s", LINE_NOT_RELAYED, reason);
    wrap_print(buf);
}

static void not_relayed_no_end(void)
{
    char why[20], f1[20], reason[80];
    if (csl_token_read(CSL_REFUSE_PATH, why, sizeof why) != 0) {
        snprintf(why, sizeof why, "none");
    }
    if (csl_token_read(CSL_F1_MISS_PATH, f1, sizeof f1) != 0) {
        snprintf(f1, sizeof f1, "none");
    }
    snprintf(reason, sizeof reason, "no_end why=%s f1=%s", why, f1);
    not_relayed(reason);
}

static int parse_status(const char *status, int *end, int *pair, int *writers,
                        int *already, unsigned *extra, unsigned *ok)
{
    int worker = 0;
    unsigned ex = 0, okv = 0;
    int n = sscanf(status,
                   "end=%d pair=%d writers=%d already=%d worker=%d extra=%u ok=%u",
                   end, pair, writers, already, &worker, &ex, &okv);
    if (n < 4) {
        return -1;
    }
    if (extra) {
        *extra = (n >= 6) ? ex : 0u;
    }
    if (ok) {
        *ok = (n >= 7) ? okv : 0u;
    }
    return 0;
}

static int control_query(const char *req, char *resp, size_t cap)
{
    int fd;
    struct sockaddr_un addr;
    ssize_t n;
    fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) {
        return -1;
    }
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    snprintf(addr.sun_path, sizeof addr.sun_path, "%s", CSL_CONTROL_SOCK);
    if (connect(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        close(fd);
        return -1;
    }
    if (csl_write_all(fd, req, strlen(req)) < 0) {
        close(fd);
        return -1;
    }
    n = read(fd, resp, cap - 1);
    close(fd);
    if (n < 0) {
        return -1;
    }
    resp[n] = 0;
    return 0;
}

static int worker_match(pid_t pid)
{
    char req[64], resp[32];
    snprintf(req, sizeof req, "WORKER_MATCH %d\n", (int)pid);
    if (control_query(req, resp, sizeof resp) < 0) {
        return 0;
    }
    return strncmp(resp, "yes", 3) == 0;
}

static const char *f4a_parent(void)
{
    /* Two accepted shapes. Never "unproven". Worker pidfd via daemon. */
    pid_t ppid = getppid();
    char exe[PATH_MAX], self[PATH_MAX];
    uid_t uid = getuid(), euid = geteuid();
    if (uid != 0 || euid != 0) {
        return "bad_parent";
    }
    /* csl_proc_exe NUL-terminates at the readlink length. Do not call
     * readlink on /proc/self/exe here: it does not append NUL, and
     * pid 0 is /proc/0/exe, not self. */
    if (csl_proc_exe(getpid(), self, sizeof self) < 0) {
        return "bad_parent";
    }
    if (csl_proc_exe(ppid, exe, sizeof exe) < 0) {
        exe[0] = 0;
    }
    if (strcmp(exe, "/usr/bin/sudo") == 0 ||
        strcmp(exe, "/usr/sbin/sudo") == 0 || strcmp(exe, "/bin/sudo") == 0) {
        /* sudo forks: sudo's ppid must equal Worker pidfd. */
        char statp[64], buf[4096];
        int fd, pp = 0;
        char state = 0;
        snprintf(statp, sizeof statp, "/proc/%d/stat", (int)ppid);
        fd = open(statp, O_RDONLY | O_CLOEXEC);
        if (fd < 0) {
            return "bad_parent";
        }
        if (read(fd, buf, sizeof buf - 1) <= 0) {
            close(fd);
            return "bad_parent";
        }
        close(fd);
        {
            char *rparen = strrchr(buf, ')');
            if (!rparen || sscanf(rparen + 2, "%c %d", &state, &pp) != 2) {
                return "bad_parent";
            }
        }
        if (!worker_match((pid_t)pp)) {
            return "bad_parent";
        }
        return NULL; /* sudo_fork */
    }
    if (strcmp(self, CSL_RELAY_PATH) == 0) {
        if (!worker_match(ppid)) {
            return "bad_parent";
        }
        return NULL; /* sudo_exec */
    }
    return "bad_parent";
}

static void pad_wall(struct timespec start)
{
    struct timespec now;
    long elapsed_ms;
    clock_gettime(CLOCK_MONOTONIC, &now);
    elapsed_ms = (long)(now.tv_sec - start.tv_sec) * 1000 +
                 (now.tv_nsec - start.tv_nsec) / 1000000;
    if (elapsed_ms < 5000) {
        struct timespec sl;
        long remain = 5000 - elapsed_ms;
        sl.tv_sec = remain / 1000;
        sl.tv_nsec = (remain % 1000) * 1000000L;
        nanosleep(&sl, NULL);
    }
}

int main(int argc, char **argv)
{
    struct timespec start;
    char status[256];
    unsigned char cap[CSL_CAP_BYTES];
    size_t n = 0;
    int capfd;
    const char *parent;
    csl_filter_result fr;
    char body[8192];
    char reconstructed[256];
    char e3[4096];
    size_t e3n = 0;
    int already = 0, end = 0, pair = 0, writers = 1;
    int waited = 0;
    unsigned extra = 0, ok = 0;

    (void)argc;
    (void)argv; /* ignore argv: do not open, stat, map, or exec {0} */
    clock_gettime(CLOCK_MONOTONIC, &start);
    (void)prctl(PR_SET_DUMPABLE, 0);
    if (freopen("/dev/null", "w", stderr) == NULL) {
        /* ignore */
    }

    parent = f4a_parent();
#ifdef CSL_PREFIX
    parent = NULL; /* unit tests compile without a Worker pidfd */
#endif
    if (parent) {
        not_relayed(parent);
        return 0;
    }

    if (control_query("STATUS\n", status, sizeof status) == 0) {
        if (parse_status(status, &end, &pair, &writers, &already, &extra, &ok) < 0) {
            writers = 1;
        }
    } else {
        end = access(CSL_END_PATH, F_OK) == 0;
        pair = access(CSL_PAIR_PATH, F_OK) == 0;
        already = access(CSL_ALREADY_PATH, F_OK) == 0;
        extra = 0;
        ok = 0;
#ifdef CSL_PREFIX
        writers = 0; /* file-based unit tests have no daemon */
#else
        writers = 1; /* N10: query fail must not fail open */
#endif
    }
    if (already) {
        not_relayed("already_ran");
        return 0;
    }
    if (!end) {
        not_relayed_no_end();
        return 0;
    }
    while (!pair && waited < CSL_WRITER_WAIT_MS) {
        struct timespec sl = {0, 50 * 1000000L};
        nanosleep(&sl, NULL);
        waited += 50;
        if (control_query("STATUS\n", status, sizeof status) == 0) {
            if (parse_status(status, &end, &pair, &writers, &already, &extra, &ok) < 0) {
                writers = 1;
            }
        } else {
            pair = access(CSL_PAIR_PATH, F_OK) == 0;
#ifdef CSL_PREFIX
            writers = 0;
#else
            writers = 1;
#endif
        }
    }
    if (!pair) {
        not_relayed("pair_open");
        return 0;
    }
    waited = 0;
    while (writers > 0 && waited < CSL_WRITER_WAIT_MS) {
        struct timespec sl = {0, 50 * 1000000L};
        nanosleep(&sl, NULL);
        waited += 50;
        if (control_query("STATUS\n", status, sizeof status) == 0) {
            if (parse_status(status, &end, &pair, &writers, &already, &extra, &ok) < 0) {
                writers = 1;
            }
        } else {
#ifdef CSL_PREFIX
            writers = 0;
#else
            writers = 1;
#endif
        }
    }
    if (writers > 0) {
        not_relayed("writer_held");
        return 0;
    }

    capfd = open(CSL_CAPTURE_PATH, O_RDONLY | O_CLOEXEC);
    if (capfd < 0) {
        not_relayed_no_end();
        return 0;
    }
    (void)csl_read_all(capfd, cap, CSL_CAP_BYTES, &n);
    close(capfd);

    fr = csl_filter_capture(cap, n);
    if (fr.status == CSL_STATUS_OBSERVED) {
        csl_reconstruct(fr.tokens, reconstructed, sizeof reconstructed);
        if (reconstructed[0] == ':' && reconstructed[1] == ':') {
            wrap_print(LINE_FILTER_ERROR);
            pad_wall(start);
            return 0;
        }
        snprintf(body, sizeof body, "%s\n", reconstructed);
    } else if (fr.status == CSL_STATUS_CONFLICT) {
        snprintf(body, sizeof body, "%s\n", LINE_CONFLICT);
    } else {
        snprintf(body, sizeof body, "%s\n", LINE_NOT_OBSERVED);
    }
    if (csl_read_file(CSL_E3_RECORD_PATH, e3, sizeof e3, &e3n) == 0 && e3n &&
        csl_e3_record_ok(e3, e3n)) {
        /* Bootstrap placeholders stay 0/false unless STATUS reported ok=
         * and extra=. Do not invent a count from pair_closed. */
        if (csl_e3_set_handoff(e3, sizeof e3, &e3n, ok, extra > 0) == 0) {
            strncat(body, e3, sizeof body - strlen(body) - 1);
            if (body[strlen(body) - 1] != '\n') {
                strncat(body, "\n", sizeof body - strlen(body) - 1);
            }
        }
    }
    strncat(body, "channel=S\nfirst_bash=missing\norigin=unattributed\n",
            sizeof body - strlen(body) - 1);
    wrap_print(body);
    (void)control_query("RAN\n", status, sizeof status);
    (void)csl_write_file(CSL_ALREADY_PATH, "1\n", 2, 0600);
    /* Shred only after gated successful print (L-a). */
    {
        int fd = open(CSL_CAPTURE_PATH, O_RDWR | O_CLOEXEC);
        if (fd >= 0) {
            struct stat st;
            unsigned char z[4096];
            memset(z, 0, sizeof z);
            if (fstat(fd, &st) == 0) {
                off_t left = st.st_size;
                while (left > 0) {
                    size_t chunk = left > (off_t)sizeof z ? sizeof z : (size_t)left;
                    if (csl_write_all(fd, z, chunk) < 0) {
                        break;
                    }
                    left -= (off_t)chunk;
                }
            }
            close(fd);
        }
        unlink(CSL_CAPTURE_PATH);
    }
    pad_wall(start);
    return 0;
}
