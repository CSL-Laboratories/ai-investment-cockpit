/* D10 capture daemon: root, empty argv, not in sudoers. Owns FIFO + sockets. */
#include "sandbox_common.h"

#include <ctype.h>
#include <dirent.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <sys/wait.h>

static volatile sig_atomic_t got_end;
static volatile sig_atomic_t pair_closed;
static volatile sig_atomic_t stop;
static int fifo_r = -1, fifo_w_held = -1, listen_fd = -1, control_fd = -1;
static int cap_fd = -1, pair_fd = -1, worker_pidfd = -1;
static int f1_armed;
static int fifo_hup;
static unsigned char capture_buf[CSL_CAP_BYTES];
static size_t captured;
static unsigned extra_handoffs;
static pid_t worker_pid;
static pid_t f1_bun_pid;

static void handle_sig(int sig)
{
    (void)sig;
    stop = 1;
}

static int bind_unix(const char *path, mode_t mode, int backlog)
{
    int fd;
    struct sockaddr_un addr;
    unlink(path);
    fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) {
        return -1;
    }
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    snprintf(addr.sun_path, sizeof addr.sun_path, "%s", path);
    if (bind(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        close(fd);
        return -1;
    }
    if (chmod(path, mode) < 0) {
        close(fd);
        return -1;
    }
    if (listen(fd, backlog) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static int fifo_writers(void)
{
    /* After F4c the daemon has closed fifo_w_held. POLLHUP means no writer
     * remains (CLI fd 2 gone). No POLLHUP means someone still holds a writer. */
    struct pollfd pfd;
    pfd.fd = fifo_r;
    pfd.events = POLLIN | POLLHUP;
    if (poll(&pfd, 1, 0) < 0) {
        return 1;
    }
    if (pfd.revents & POLLHUP) {
        return 0;
    }
    return 1;
}

static int cmdline_tokens(pid_t pid, char tokens[][512], int cap)
{
    char buf[4096];
    size_t n = 0, off = 0;
    int i = 0;
    if (csl_proc_cmdline(pid, buf, sizeof buf, &n) < 0) {
        return -1;
    }
    while (off < n && i < cap) {
        size_t len = strlen(buf + off);
        if (len >= 512) {
            return -1;
        }
        memcpy(tokens[i], buf + off, len + 1);
        i++;
        off += len + 1;
    }
    return i;
}

static pid_t proc_ppid(pid_t pid)
{
    return csl_proc_ppid(pid);
}

static int count_children(pid_t parent)
{
    DIR *d = opendir("/proc");
    struct dirent *ent;
    int n = 0;
    if (!d) {
        return -1;
    }
    while ((ent = readdir(d))) {
        pid_t pid;
        if (!isdigit((unsigned char)ent->d_name[0])) {
            continue;
        }
        pid = (pid_t)atoi(ent->d_name);
        if (pid > 0 && proc_ppid(pid) == parent) {
            n++;
        }
    }
    closedir(d);
    return n;
}

static int is_uuid_sh(const char *s, const char *temp)
{
    size_t tl, i;
    const char *name;
    if (!s || !temp || !temp[0]) {
        return 0;
    }
    tl = strlen(temp);
    if (strncmp(s, temp, tl) != 0 || s[tl] != '/') {
        return 0;
    }
    name = s + tl + 1;
    if (strlen(name) != 39 || strcmp(name + 36, ".sh") != 0) {
        return 0;
    }
    for (i = 0; i < 36; i++) {
        char c = name[i];
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (c != '-') {
                return 0;
            }
        } else if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
            return 0;
        }
    }
    return 1;
}

static int bun_cmdline_ok(char tokens[][512], int ntok)
{
    char cfg[768], runp[768];
    if (ntok < 5) {
        return 0;
    }
    if (strcmp(tokens[0], CSL_BUN_PATH) != 0 && strcmp(tokens[0], "bun") != 0 &&
        !strstr(tokens[0], "/bun")) {
        return 0;
    }
    if (strcmp(tokens[1], "--no-env-file") != 0 || strcmp(tokens[3], "run") != 0) {
        return 0;
    }
    snprintf(cfg, sizeof cfg, "--config=%s/bunfig.toml", CSL_ACTION_DIR);
    snprintf(runp, sizeof runp, "%s/src/entrypoints/run.ts", CSL_ACTION_DIR);
    return strcmp(tokens[2], cfg) == 0 && strcmp(tokens[4], runp) == 0;
}

static int bash_b_ok(pid_t b, pid_t x)
{
    char exe[PATH_MAX];
    char tokens[12][512];
    int ntok;
    const char *temp = CSL_RUNNER_TEMP;
    pid_t bpp;
    if (csl_proc_exe(b, exe, sizeof exe) < 0) {
        return 0;
    }
    if (strcmp(exe, "/usr/bin/bash") != 0 && strcmp(exe, "/bin/bash") != 0) {
        return 0;
    }
    ntok = cmdline_tokens(b, tokens, 12);
    if (ntok < 7) {
        return 0;
    }
    if (strcmp(tokens[0], "/bin/bash") != 0 && strcmp(tokens[0], "/usr/bin/bash") != 0) {
        return 0;
    }
    if (strcmp(tokens[1], "--noprofile") != 0 || strcmp(tokens[2], "--norc") != 0 ||
        strcmp(tokens[3], "-e") != 0 || strcmp(tokens[4], "-o") != 0 ||
        strcmp(tokens[5], "pipefail") != 0) {
        return 0;
    }
    if (!is_uuid_sh(tokens[6], temp ? temp : "")) {
        return 0;
    }
    bpp = proc_ppid(b);
    if (worker_pidfd >= 0) {
        pid_t held = 0;
        if (csl_read_pidfd_pid(worker_pidfd, &held) < 0 || held != bpp || bpp != worker_pid) {
            return 0;
        }
    } else if (bpp != worker_pid) {
        return 0;
    }
    if (count_children(b) != 1) {
        return 0;
    }
    (void)x;
    return 1;
}

static int hash_pid_exe(pid_t pid, unsigned char dig[32])
{
    char path[64];
    int fd, rc;
    snprintf(path, sizeof path, "/proc/%d/exe", (int)pid);
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        return -1;
    }
    rc = csl_sha256_fd(fd, dig);
    close(fd);
    return rc;
}

static int bun_tofu_match(pid_t pid)
{
    unsigned char dig[32];
    char hex[65], want[80];
    size_t n = 0;
    if (hash_pid_exe(pid, dig) != 0) {
        return 0;
    }
    csl_hex32(dig, hex);
    if (csl_read_file(CSL_BUN_TOFU_PATH, want, sizeof want, &n) != 0 || n < 64) {
        return 0;
    }
    want[64] = 0;
    return strncmp(hex, want, 64) == 0;
}

static int bun_name_recorded(const char *name)
{
    char buf[4096];
    size_t n = 0, off = 0;
    if (csl_read_file(CSL_STATE_DIR "/bun.names", buf, sizeof buf, &n) != 0) {
        return 0;
    }
    while (off < n) {
        char *nl = strchr(buf + off, '\n');
        size_t len = nl ? (size_t)(nl - (buf + off)) : strlen(buf + off);
        if (len == strlen(name) && memcmp(buf + off, name, len) == 0) {
            return 1;
        }
        if (!nl) {
            break;
        }
        off += len + 1;
    }
    return 0;
}

static int x_environ_ok(pid_t pid)
{
    char path[64], buf[65536];
    int fd;
    ssize_t n;
    size_t off = 0;
    snprintf(path, sizeof path, "/proc/%d/environ", (int)pid);
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        return 0;
    }
    n = read(fd, buf, sizeof buf - 1);
    close(fd);
    if (n <= 0) {
        return 0;
    }
    buf[n] = 0;
    while (off < (size_t)n) {
        const char *e = buf + off;
        size_t elen = strlen(e);
        if (strncmp(e, "LD_PRELOAD=", 11) == 0 && e[11]) {
            return 0;
        }
        if (strncmp(e, "LD_AUDIT=", 9) == 0 && e[9]) {
            return 0;
        }
        if (strncmp(e, "LD_LIBRARY_PATH=", 16) == 0 && e[16]) {
            return 0;
        }
        if (strncmp(e, "SRT_DEBUG=", 10) == 0 ||
            strncmp(e, "DEBUG_CLAUDE_AGENT_SDK=", 23) == 0 ||
            strncmp(e, "ACTIONS_STEP_DEBUG=", 19) == 0) {
            return 0;
        }
        if (strncmp(e, "BUN_", 4) == 0) {
            char key[128];
            const char *eq = strchr(e, '=');
            size_t klen;
            if (!eq) {
                return 0;
            }
            klen = (size_t)(eq - e);
            if (klen >= sizeof key) {
                return 0;
            }
            memcpy(key, e, klen);
            key[klen] = 0;
            if (!bun_name_recorded(key)) {
                return 0;
            }
        }
        off += elen + 1;
    }
    return 1;
}

/* F1: observe bun X under parent B, then chmod handoff 0666. */
static void try_arm_f1(void)
{
    DIR *d;
    struct dirent *ent;
    char cwdwant[512];
    size_t cn = 0;
    if (f1_armed) {
        return;
    }
    if (csl_read_file(CSL_WORKSPACE_PATH, cwdwant, sizeof cwdwant, &cn) != 0) {
        return;
    }
    while (cn && (cwdwant[cn - 1] == '\n' || cwdwant[cn - 1] == ' ')) {
        cwdwant[--cn] = 0;
    }
    if (cn == 0) {
        return;
    }
    d = opendir("/proc");
    if (!d) {
        return;
    }
    while ((ent = readdir(d))) {
        pid_t x, b;
        char exe[PATH_MAX], cwd[PATH_MAX], link[64];
        char tokens[12][512];
        int ntok;
        if (!isdigit((unsigned char)ent->d_name[0])) {
            continue;
        }
        x = (pid_t)atoi(ent->d_name);
        if (x <= 0) {
            continue;
        }
        if (csl_proc_exe(x, exe, sizeof exe) < 0) {
            continue;
        }
        if (strcmp(exe, CSL_BUN_PATH) != 0) {
            continue;
        }
        ntok = cmdline_tokens(x, tokens, 12);
        if (ntok < 0 || !bun_cmdline_ok(tokens, ntok)) {
            continue;
        }
        snprintf(link, sizeof link, "/proc/%d/cwd", (int)x);
        {
            ssize_t n = readlink(link, cwd, sizeof cwd - 1);
            if (n < 0) {
                continue;
            }
            cwd[n] = 0;
        }
        if (strcmp(cwd, cwdwant) != 0) {
            continue;
        }
        b = proc_ppid(x);
        if (!b || !bash_b_ok(b, x)) {
            continue;
        }
        if (!bun_tofu_match(x)) {
            continue;
        }
        if (!x_environ_ok(x)) {
            continue;
        }
        if (chmod(CSL_HANDOFF_SOCK, 0666) == 0) {
            f1_armed = 1;
            f1_bun_pid = x;
            (void)csl_write_file(CSL_STATE_DIR "/f1.armed", "1\n", 2, 0600);
        }
        break;
    }
    closedir(d);
}

static int f2_launcher_parent_is_bun(pid_t launcher)
{
#ifdef CSL_PREFIX
    (void)launcher;
    return 1;
#else
    /* G8: pidfd_open(ppid), re-check launcher alive + PPid, hash exe, pidfd live. */
    pid_t x, x2;
    int xfd;
    pid_t held = 0;
    if (f1_bun_pid <= 0) {
        return 0;
    }
    x = proc_ppid(launcher);
    if (x <= 0 || x != f1_bun_pid) {
        return 0;
    }
    xfd = csl_pidfd_open(x, 0);
    if (xfd < 0) {
        return 0;
    }
    if (kill(launcher, 0) < 0) {
        close(xfd);
        return 0;
    }
    x2 = proc_ppid(launcher);
    if (x2 != x) {
        close(xfd);
        return 0;
    }
    if (!bun_tofu_match(x)) {
        close(xfd);
        return 0;
    }
    if (csl_read_pidfd_pid(xfd, &held) < 0 || held != x) {
        close(xfd);
        return 0;
    }
    close(xfd);
    return 1;
#endif
}

static void drain_fifo(int discard)
{
    unsigned char buf[8192];
    ssize_t r;
    /* Loop until the FIFO is empty (EAGAIN) or EOF. A single 8 KiB read
     * drops queued stderr when END/POLLHUP arrives with more than one
     * chunk still buffered. */
    for (;;) {
        r = read(fifo_r, buf, sizeof buf);
        if (r < 0) {
            if (errno == EINTR) {
                continue;
            }
            return;
        }
        if (r == 0) {
            return;
        }
        if (discard || captured >= CSL_CAP_BYTES) {
            continue;
        }
        if (captured + (size_t)r > CSL_CAP_BYTES) {
            size_t keep = CSL_CAP_BYTES - captured;
            memcpy(capture_buf + captured, buf, keep);
            captured = CSL_CAP_BYTES;
            if (cap_fd >= 0) {
                (void)csl_write_all(cap_fd, buf, keep);
            }
            continue;
        }
        memcpy(capture_buf + captured, buf, (size_t)r);
        captured += (size_t)r;
        if (cap_fd >= 0) {
            (void)csl_write_all(cap_fd, buf, (size_t)r);
        }
    }
}

static void close_rights(struct msghdr *msg, int keep)
{
    struct cmsghdr *cmsg;
    for (cmsg = CMSG_FIRSTHDR(msg); cmsg; cmsg = CMSG_NXTHDR(msg, cmsg)) {
        if (cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SCM_RIGHTS) {
            int nfds = (int)((cmsg->cmsg_len - CMSG_LEN(0)) / sizeof(int));
            int *fds = (int *)CMSG_DATA(cmsg);
            int i;
            for (i = 0; i < nfds; i++) {
                if (fds[i] != keep && fds[i] >= 0) {
                    close(fds[i]);
                }
            }
        }
    }
}

static int peer_uid_ok(uid_t uid)
{
#ifdef CSL_PREFIX
    return uid == getuid();
#else
    uid_t runner = 0;
    if (csl_lookup_name("runner", &runner) != 0) {
        return 0;
    }
    return uid == runner;
#endif
}

static void accept_handoff(void)
{
    int cfd = accept4(listen_fd, NULL, NULL, SOCK_CLOEXEC);
    struct msghdr msg;
    struct iovec iov;
    char cmsgbuf[CMSG_SPACE(sizeof(int)) * 4];
    char dummy = 0;
    struct cmsghdr *cmsg;
    int got_pair = -1;
    int saw_cred = 0;
    struct ucred peer;
    socklen_t plen = sizeof peer;
    int pass = 1;
    struct timeval rcv = {.tv_sec = 2, .tv_usec = 0};

    if (cfd < 0) {
        return;
    }
    extra_handoffs++;
    /* Blocking accept + SO_RCVTIMEO: connect-before-sendmsg must not EAGAIN. */
    if (setsockopt(cfd, SOL_SOCKET, SO_RCVTIMEO, &rcv, sizeof rcv) < 0) {
        close(cfd);
        return;
    }
    /* N11: do not unlink until the peer is verified. */
    if (setsockopt(cfd, SOL_SOCKET, SO_PASSCRED, &pass, sizeof pass) < 0) {
        close(cfd);
        return;
    }
    if (getsockopt(cfd, SOL_SOCKET, SO_PEERCRED, &peer, &plen) < 0) {
        close(cfd);
        return;
    }
    if (!peer_uid_ok(peer.uid)) {
        close(cfd);
        return;
    }
    memset(&msg, 0, sizeof msg);
    iov.iov_base = &dummy;
    iov.iov_len = 1;
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    msg.msg_control = cmsgbuf;
    msg.msg_controllen = sizeof cmsgbuf;
    if (recvmsg(cfd, &msg, 0) < 0) {
        close(cfd);
        return;
    }
    if (msg.msg_flags & MSG_CTRUNC) {
        close_rights(&msg, -1);
        close(cfd);
        return;
    }
    for (cmsg = CMSG_FIRSTHDR(&msg); cmsg; cmsg = CMSG_NXTHDR(&msg, cmsg)) {
        if (cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SCM_RIGHTS) {
            int nfds = (int)((cmsg->cmsg_len - CMSG_LEN(0)) / sizeof(int));
            int *fds = (int *)CMSG_DATA(cmsg);
            if (nfds != 1 || got_pair >= 0) {
                close_rights(&msg, -1);
                close(cfd);
                return;
            }
            got_pair = fds[0];
        }
        if (cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SCM_CREDENTIALS) {
            struct ucred scm;
            memcpy(&scm, CMSG_DATA(cmsg), sizeof scm);
            if (scm.pid != peer.pid || scm.uid != peer.uid) {
                close_rights(&msg, -1);
                close(cfd);
                return;
            }
            saw_cred = 1;
        }
    }
    if (got_pair < 0 || !saw_cred) {
        close_rights(&msg, -1);
        close(cfd);
        return;
    }
    /* F5: SO_PEERPIDFD identity. Fail closed if unavailable. */
    {
        int peerpidfd = -1;
        socklen_t sl = sizeof peerpidfd;
        char exe[PATH_MAX];
        unsigned char dig[32];
        char pinhex[80];
        size_t pn = 0;
        pid_t pfd_pid = 0;
        if (getsockopt(cfd, SOL_SOCKET, SO_PEERPIDFD, &peerpidfd, &sl) < 0) {
            close(cfd);
            close(got_pair);
            return;
        }
        if (csl_read_pidfd_pid(peerpidfd, &pfd_pid) < 0 || pfd_pid != peer.pid) {
            close(peerpidfd);
            close(cfd);
            close(got_pair);
            return;
        }
        if (csl_proc_exe(peer.pid, exe, sizeof exe) < 0) {
            close(peerpidfd);
            close(cfd);
            close(got_pair);
            return;
        }
        if (strcmp(exe, CSL_LAUNCH_PATH) != 0) {
            close(peerpidfd);
            close(cfd);
            close(got_pair);
            return;
        }
        if (csl_sha256_file(exe, dig) != 0 ||
            csl_read_file(CSL_LAUNCH_SHA_PATH, pinhex, sizeof pinhex, &pn) != 0 ||
            pn == 0) {
            close(peerpidfd);
            close(cfd);
            close(got_pair);
            return;
        }
        while (pn && (pinhex[pn - 1] == '\n' || pinhex[pn - 1] == '\r' ||
                      pinhex[pn - 1] == ' ')) {
            pinhex[--pn] = 0;
        }
        if (!csl_hex_eq(dig, pinhex)) {
            close(peerpidfd);
            close(cfd);
            close(got_pair);
            return;
        }
        if (!f1_armed || !f2_launcher_parent_is_bun(peer.pid)) {
            close(peerpidfd);
            close(cfd);
            close(got_pair);
            return;
        }
        close(peerpidfd);
    }
    unlink(CSL_HANDOFF_SOCK);
    /* Send FIFO write fd over the pair only (F4c: close our write copy after). */
    {
        int wfd = open(CSL_FIFO_PATH, O_WRONLY | O_CLOEXEC);
        struct msghdr sm;
        struct iovec siov;
        char scbuf[CMSG_SPACE(sizeof(int))];
        struct cmsghdr *sc;
        char d = 'S';
        if (wfd < 0) {
            close(cfd);
            close(got_pair);
            return;
        }
        memset(&sm, 0, sizeof sm);
        siov.iov_base = &d;
        siov.iov_len = 1;
        sm.msg_iov = &siov;
        sm.msg_iovlen = 1;
        sm.msg_control = scbuf;
        sm.msg_controllen = sizeof scbuf;
        sc = CMSG_FIRSTHDR(&sm);
        sc->cmsg_level = SOL_SOCKET;
        sc->cmsg_type = SCM_RIGHTS;
        sc->cmsg_len = CMSG_LEN(sizeof(int));
        memcpy(CMSG_DATA(sc), &wfd, sizeof wfd);
        if (sendmsg(got_pair, &sm, 0) < 0) {
            close(wfd);
            close(cfd);
            close(got_pair);
            return;
        }
        close(wfd); /* F4c send copy */
        if (fifo_w_held >= 0) {
            close(fifo_w_held);
            fifo_w_held = -1;
        }
    }
    pair_fd = got_pair;
    extra_handoffs--; /* first successful peer is the launcher */
    close(cfd);
}

static void read_pair(void)
{
    char buf[64];
    ssize_t r;
    if (pair_fd < 0) {
        return;
    }
    r = read(pair_fd, buf, sizeof buf);
    if (r <= 0) {
        pair_closed = 1;
        (void)csl_write_file(CSL_PAIR_PATH, "1\n", 2, 0600);
        close(pair_fd);
        pair_fd = -1;
        return;
    }
    if (r >= 4 && memcmp(buf, "END\n", 4) == 0) {
        got_end = 1;
        (void)csl_write_file(CSL_END_PATH, "ok\n", 3, 0600);
    }
}

static void handle_control(void)
{
    int cfd = accept4(control_fd, NULL, NULL, SOCK_CLOEXEC);
    char req[128], resp[256];
    ssize_t n;
    if (cfd < 0) {
        return;
    }
    n = read(cfd, req, sizeof req - 1);
    if (n <= 0) {
        close(cfd);
        return;
    }
    req[n] = 0;
    if (strncmp(req, "STATUS", 6) == 0) {
        snprintf(resp, sizeof resp, "end=%d pair=%d writers=%d already=%d worker=%d extra=%u\n",
                 got_end ? 1 : 0, pair_closed ? 1 : 0, fifo_writers(),
                 access(CSL_ALREADY_PATH, F_OK) == 0 ? 1 : 0, (int)worker_pid,
                 extra_handoffs);
        (void)csl_write_all(cfd, resp, strlen(resp));
    } else if (strncmp(req, "RAN", 3) == 0) {
        (void)csl_write_file(CSL_ALREADY_PATH, "1\n", 2, 0600);
        (void)csl_write_all(cfd, "ok\n", 3);
    } else if (strncmp(req, "WORKER_MATCH ", 13) == 0) {
        pid_t q = (pid_t)atoi(req + 13);
        pid_t held = 0;
        int ok = 0;
        if (worker_pidfd >= 0 && csl_read_pidfd_pid(worker_pidfd, &held) == 0) {
            ok = (held == q && q == worker_pid);
        }
        (void)csl_write_all(cfd, ok ? "yes\n" : "no\n", ok ? 4 : 3);
    } else {
        (void)csl_write_all(cfd, "err\n", 4);
    }
    close(cfd);
}

static pid_t load_worker(void)
{
    char buf[64], exe[PATH_MAX];
    size_t n = 0;
    pid_t pid;
    const char *base;
    if (csl_read_file(CSL_WORKER_PID_PATH, buf, sizeof buf, &n) != 0 || n == 0) {
        return 0;
    }
    pid = (pid_t)atoi(buf);
    if (pid <= 0) {
        return 0;
    }
#ifdef CSL_PREFIX
    (void)exe;
    (void)base;
    return pid;
#else
    if (csl_proc_exe(pid, exe, sizeof exe) < 0) {
        return 0;
    }
    base = strrchr(exe, '/');
    base = base ? base + 1 : exe;
    if (strcmp(base, "Runner.Worker") != 0) {
        return 0;
    }
    return pid;
#endif
}

#ifndef CSL_FIFO_DRAIN_TEST
int main(int argc, char **argv)
{
    struct pollfd pfds[4];
    (void)argv;
    if (argc != 1) {
        /* Empty argv only. Refuse extras. */
        csl_die(1, "argv");
    }
    if (prctl(PR_SET_DUMPABLE, 0) < 0) {
        csl_die(1, "dumpable");
    }
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, handle_sig);

    if (csl_mkdir_p(CSL_STATE_DIR, 0711) < 0) {
        csl_die(1, "mkdir");
    }
    chmod(CSL_STATE_DIR, 0711);

    unlink(CSL_FIFO_PATH);
    if (mkfifo(CSL_FIFO_PATH, 0600) < 0) {
        csl_die(1, "fifo");
    }
    if (chown(CSL_FIFO_PATH, 0, 0) < 0 && errno != EPERM) {
        csl_die(1, "fifo_chown");
    }
    chmod(CSL_FIFO_PATH, 0600);

    cap_fd = open(CSL_CAPTURE_PATH, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (cap_fd < 0) {
        csl_die(1, "capture");
    }

    fifo_r = open(CSL_FIFO_PATH, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fifo_r < 0) {
        csl_die(1, "fifo_r");
    }
    /* Hold a write fd so readers do not see EOF before the launcher connects.
     * Closed after the first hand-off send (F4c uses a separate write fd). */
    fifo_w_held = open(CSL_FIFO_PATH, O_WRONLY | O_NONBLOCK | O_CLOEXEC);
    if (fifo_w_held < 0) {
        csl_die(1, "fifo_w");
    }

    worker_pid = load_worker();
    if (worker_pid <= 0) {
        csl_die(1, "worker");
    }
    worker_pidfd = csl_pidfd_open(worker_pid, 0);
    if (worker_pidfd < 0) {
        csl_die(1, "worker_pidfd");
    }

    listen_fd = bind_unix(CSL_HANDOFF_SOCK, 0600, 1);
    if (listen_fd < 0) {
        csl_die(1, "handoff");
    }
    control_fd = bind_unix(CSL_CONTROL_SOCK, 0600, 4);
    if (control_fd < 0) {
        csl_die(1, "control");
    }

#ifdef CSL_PREFIX
    /* Test compile only: skip F1 wait so socket tests can connect. */
    chmod(CSL_HANDOFF_SOCK, 0666);
    f1_armed = 1;
#else
    /* Production: stay 0600 until F1 bun X is observed (try_arm_f1). */
    f1_armed = 0;
#endif

    for (;;) {
        int nfd = 0;
        int fifo_idx = -1, listen_idx, control_idx, pair_idx = -1;
        if (stop) {
            break;
        }
        if (!fifo_hup) {
            fifo_idx = nfd;
            pfds[nfd].fd = fifo_r;
            pfds[nfd].events = POLLIN | POLLHUP;
            nfd++;
        }
        listen_idx = nfd;
        pfds[nfd].fd = listen_fd;
        pfds[nfd].events = POLLIN;
        nfd++;
        control_idx = nfd;
        pfds[nfd].fd = control_fd;
        pfds[nfd].events = POLLIN;
        nfd++;
        if (pair_fd >= 0) {
            pair_idx = nfd;
            pfds[nfd].fd = pair_fd;
            pfds[nfd].events = POLLIN | POLLHUP;
            nfd++;
        }
        if (!f1_armed) {
            try_arm_f1();
        }
        if (poll(pfds, nfd, f1_armed ? 500 : 50) < 0) {
            if (errno == EINTR) {
                continue;
            }
            break;
        }
        if (fifo_idx >= 0 && (pfds[fifo_idx].revents & (POLLIN | POLLHUP))) {
            /* Drain queued FIFO bytes before treating END/POLLHUP as final.
             * got_end must not discard pre-exit stderr still sitting in the
             * pipe; only the capture cap discards. */
            drain_fifo(captured >= CSL_CAP_BYTES);
            if (pfds[fifo_idx].revents & POLLHUP) {
                drain_fifo(captured >= CSL_CAP_BYTES);
                fifo_hup = 1;
            }
        }
        if (pfds[listen_idx].revents & POLLIN) {
            accept_handoff();
        }
        if (pfds[control_idx].revents & POLLIN) {
            handle_control();
        }
        if (pair_idx >= 0 && (pfds[pair_idx].revents & (POLLIN | POLLHUP))) {
            read_pair();
        }
    }
    return 0;
}
#endif

#ifdef CSL_FIFO_DRAIN_TEST
int main(void)
{
    int pfd[2];
    unsigned char payload[32768];
    size_t i;
    ssize_t w;
    int flags;

    for (i = 0; i < sizeof payload; i++) {
        payload[i] = (unsigned char)(i & 0xff);
    }
    if (pipe(pfd) < 0) {
        return 1;
    }
    flags = fcntl(pfd[0], F_GETFL, 0);
    if (flags < 0 || fcntl(pfd[0], F_SETFL, flags | O_NONBLOCK) < 0) {
        return 2;
    }
    fifo_r = pfd[0];
    cap_fd = -1;
    captured = 0;
    (void)fifo_hup;
    w = write(pfd[1], payload, sizeof payload);
    if (w != (ssize_t)sizeof payload) {
        return 3;
    }
    close(pfd[1]);
    /* END arrives before the FIFO is drained; queued bytes must still land. */
    got_end = 1;
    drain_fifo(captured >= CSL_CAP_BYTES);
    if (captured != sizeof payload) {
        return 4;
    }
    if (memcmp(capture_buf, payload, sizeof payload) != 0) {
        return 5;
    }
    close(pfd[0]);
    return 0;
}
#endif
