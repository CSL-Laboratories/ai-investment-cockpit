/* D10 E3-2 static launcher. Runner-uid. Not in sudoers. */
#include "sandbox_argv.h"

#include <dirent.h>
#include <limits.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>

extern char **environ;

static void refuse(const char *why)
{
    char tok[17];
    char rec[18];
    int n;
    if (csl_token_copy(why, tok, sizeof tok) == 0) {
        n = snprintf(rec, sizeof rec, "%s\n", tok);
        if (n > 0 && (size_t)n < sizeof rec) {
            (void)csl_write_file(CSL_REFUSE_PATH, rec, (size_t)n, 0600);
        }
    }
    if (write(2, "launcher refused\n", 17) < 0) {
        /* ignore */
    }
    _exit(1);
}

static int u15_refuse(void)
{
    if (csl_env_present("SRT_DEBUG") || csl_env_present("DEBUG_CLAUDE_AGENT_SDK") ||
        csl_env_present("ACTIONS_STEP_DEBUG")) {
        return 1;
    }
    if (csl_truthy(getenv("RUNNER_DEBUG"))) {
        return 1;
    }
    return 0;
}

static char **build_child_env(char *home, char *user)
{
    static char *envp[64];
    static char storage[64][512];
    int n = 0;
#define ADD(k, v)                                                              \
    do {                                                                       \
        if (n >= 63) {                                                         \
            refuse("env");                                                     \
        }                                                                      \
        snprintf(storage[n], sizeof storage[n], "%s=%s", k, v);                \
        envp[n] = storage[n];                                                  \
        n++;                                                                   \
    } while (0)
    ADD("SRT_DEBUG", "1");
    ADD("DISABLE_UPDATES", "1");
    ADD("PATH", "/usr/bin:/bin");
    ADD("LANG", "C.UTF-8");
    ADD("LC_ALL", "C.UTF-8");
    ADD("TERM", "dumb");
    ADD("HOME", home);
    ADD("USER", user);
    ADD("LOGNAME", user);
    if (getenv("TZ")) {
        ADD("TZ", getenv("TZ"));
    }
    if (getenv("CLAUDE_CODE_OAUTH_TOKEN")) {
        ADD("CLAUDE_CODE_OAUTH_TOKEN", getenv("CLAUDE_CODE_OAUTH_TOKEN"));
    }
    if (!getenv("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB") ||
        strcmp(getenv("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"), "1") != 0) {
        refuse("scrub");
    }
    ADD("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "1");
    if (getenv("CLAUDE_CODE_ENTRYPOINT")) {
        ADD("CLAUDE_CODE_ENTRYPOINT", getenv("CLAUDE_CODE_ENTRYPOINT"));
    }
    if (getenv("GITHUB_TOKEN")) {
        ADD("GITHUB_TOKEN", getenv("GITHUB_TOKEN"));
    }
    if (getenv("GH_TOKEN")) {
        ADD("GH_TOKEN", getenv("GH_TOKEN"));
    }
    ADD("RUNNER_TEMP", CSL_RUNNER_TEMP);
    if (getenv("RUNNER_OS")) {
        ADD("RUNNER_OS", getenv("RUNNER_OS"));
    }
    if (getenv("CI")) {
        ADD("CI", getenv("CI"));
    }
    if (getenv("GITHUB_ACTIONS")) {
        ADD("GITHUB_ACTIONS", getenv("GITHUB_ACTIONS"));
    }
    {
        char **e;
        for (e = environ; *e; e++) {
            if (strncmp(*e, "GITHUB_", 7) == 0) {
                const char *name = *e;
                if (strncmp(name, "GITHUB_ENV=", 11) == 0 ||
                    strncmp(name, "GITHUB_PATH=", 12) == 0 ||
                    strncmp(name, "GITHUB_OUTPUT=", 14) == 0 ||
                    strncmp(name, "GITHUB_STATE=", 13) == 0 ||
                    strncmp(name, "GITHUB_STEP_SUMMARY=", 20) == 0) {
                    continue;
                }
                if (n >= 63) {
                    refuse("env");
                }
                snprintf(storage[n], sizeof storage[n], "%s", *e);
                envp[n] = storage[n];
                n++;
            }
        }
    }
    envp[n] = NULL;
    return envp;
#undef ADD
}

static int mcp_exists(const char *rel)
{
    char path[PATH_MAX];
    struct stat st;
    if (getcwd(path, sizeof path) == NULL) {
        return 1;
    }
    if (strlen(path) + strlen(rel) + 2 >= sizeof path) {
        return 1;
    }
    strcat(path, rel);
    return lstat(path, &st) == 0;
}

static int claude_dir_ok(const char *home)
{
    char dir[PATH_MAX], json[PATH_MAX];
    DIR *d;
    struct dirent *ent;
    int saw_settings = 0;
    snprintf(dir, sizeof dir, "%s/.claude", home);
    snprintf(json, sizeof json, "%s/.claude.json", home);
    {
        struct stat st;
        if (lstat(json, &st) == 0) {
            return 0;
        }
    }
    d = opendir(dir);
    if (!d) {
        return 0;
    }
    while ((ent = readdir(d))) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) {
            continue;
        }
        if (strcmp(ent->d_name, "settings.json") == 0) {
            saw_settings = 1;
            continue;
        }
        /* Fail closed on CLAUDE.md, agents, commands, skills, plugins, hooks. */
        closedir(d);
        return 0;
    }
    closedir(d);
    return saw_settings;
}

/* NULL when the launcher-owned empty MCP file is usable, else the refuse
 * token naming the failed check. */
static const char *mcp_empty_why(void)
{
    char buf[64];
    size_t n = 0;
    size_t want = strlen(CSL_MCP_EMPTY_BYTES);
    if (csl_read_file(CSL_MCP_EMPTY_PATH, buf, sizeof buf, &n) != 0) {
        return "mcp_read";
    }
    if (n != want || memcmp(buf, CSL_MCP_EMPTY_BYTES, want) != 0) {
        return "mcp_bytes";
    }
#ifndef CSL_PREFIX
    if (!csl_ancestors_root_owned(CSL_MCP_EMPTY_PATH)) {
        return "mcp_own";
    }
#endif
    return NULL;
}

static int connect_handoff(void)
{
    int fd;
    struct sockaddr_un addr;
    fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) {
        return -1;
    }
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    snprintf(addr.sun_path, sizeof addr.sun_path, "%s", CSL_HANDOFF_SOCK);
    if (connect(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static long monotonic_ms(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
        return -1;
    }
    return ts.tv_sec * 1000L + ts.tv_nsec / 1000000L;
}

static void sleep_ms(long ms)
{
    struct timespec sl;
    sl.tv_sec = ms / 1000;
    sl.tv_nsec = (ms % 1000) * 1000000L;
    (void)nanosleep(&sl, NULL);
}

static int parse_ppid(pid_t pid, pid_t *ppid)
{
    char path[64];
    char buf[512];
    size_t n = 0;
    char *rparen;
    char *p;
    char *end = NULL;
    long v;
    snprintf(path, sizeof path, "/proc/%ld/stat", (long)pid);
    if (csl_read_file(path, buf, sizeof buf, &n) != 0 || n == 0) {
        return -1;
    }
    if (n >= sizeof buf) {
        n = sizeof buf - 1;
    }
    buf[n] = 0;
    rparen = strrchr(buf, ')');
    if (!rparen) {
        return -1;
    }
    p = rparen + 1;
    while (*p == ' ') {
        p++;
    }
    if (!*p) {
        return -1;
    }
    while (*p && *p != ' ') {
        p++;
    }
    while (*p == ' ') {
        p++;
    }
    v = strtol(p, &end, 10);
    if (!end || end == p || v <= 0) {
        return -1;
    }
    *ppid = (pid_t)v;
    return 0;
}

static int kill_remaining_children(void)
{
    DIR *d;
    struct dirent *e;
    pid_t self = getpid();
    int work = 0;
    int st = 0;
    d = opendir("/proc");
    if (!d) {
        return 0;
    }
    while ((e = readdir(d))) {
        char *end = NULL;
        pid_t pid;
        pid_t ppid;
        pid_t wr;
        if (e->d_name[0] < '1' || e->d_name[0] > '9') {
            continue;
        }
        pid = (pid_t)strtol(e->d_name, &end, 10);
        if (!end || *end || pid <= 1 || pid == self) {
            continue;
        }
        if (parse_ppid(pid, &ppid) != 0 || ppid != self) {
            continue;
        }
        /* kill() on a zombie returns 0, so reap first and do not count a
         * kill. Reaping is still work: the caller sleeps and rescans for
         * children adopted after the zombie parent is gone (PPid == self). */
        wr = waitpid(pid, &st, WNOHANG);
        if (wr == pid) {
            work++;
            continue;
        }
        if (kill(pid, SIGKILL) == 0) {
            work++;
        }
    }
    closedir(d);
    return work;
}

static void reap_wnohang_until(long deadline_ms, int cap)
{
    int left = cap;
    int st = 0;
    while (left > 0) {
        pid_t r = waitpid(-1, &st, WNOHANG);
        if (r < 0) {
            break;
        }
        if (r == 0) {
            long now = monotonic_ms();
            if (now < 0 || now >= deadline_ms) {
                break;
            }
            sleep_ms(10);
            continue;
        }
        left--;
    }
}

void csl_reap_leftover_descendants(pid_t child)
{
    long start = monotonic_ms();
    long deadline = (start < 0) ? 0 : start + 3000;
    int round;
    int st = 0;
    reap_wnohang_until(deadline, 256);
    (void)kill(-child, SIGKILL);
    for (round = 0; round < 32; round++) {
        int n = kill_remaining_children();
        while (waitpid(-1, &st, WNOHANG) > 0) {
        }
        if (n == 0) {
            break;
        }
        sleep_ms(10);
    }
    start = monotonic_ms();
    deadline = (start < 0) ? 0 : start + 200;
    reap_wnohang_until(deadline, 256);
}

static void close_cmsg_fds(struct msghdr *msg, int keep)
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

#ifndef CSL_N14_TEST
int main(int argc, char **argv)
{
    char home[256], user[64];
    char **child_env;
    char *cargv[64];
    int pair[2], handoff, sink = -1;
    unsigned char digest[32];
    int claude_fd;
    pid_t child;
    int status = 1;
    struct iovec iov;
    struct msghdr msg;
    char cmsgbuf[CMSG_SPACE(sizeof(int)) + CMSG_SPACE(sizeof(struct ucred))];
    struct cmsghdr *cmsg;
    char dummy = 'H';
    struct ucred cred;
    int rewritten = 0;

    if (argc > 62) {
        refuse("argc");
    }
    if (getenv("LD_PRELOAD") || getenv("LD_AUDIT")) {
        refuse("ld");
    }
    if (u15_refuse()) {
        refuse("u15");
    }
    if (csl_argv_allow(argc, argv) != 0) {
        refuse("bad_argv");
    }
    if (csl_argv_rewrite(argc, argv, cargv, 64, &rewritten) != 0 || rewritten > 62) {
        refuse("bad_argv");
    }
    if (csl_lookup_user(getuid(), user, sizeof user, home, sizeof home) != 0) {
        refuse("passwd");
    }
    if (prctl(PR_SET_DUMPABLE, 0) < 0) {
        refuse("dumpable");
    }
    child_env = build_child_env(home, user);

    /* N1: cwd .mcp.json / .claude/settings.json are ignored because the
     * child is exec'd with --setting-sources user and injected
     * --strict-mcp-config --mcp-config CSL_MCP_EMPTY_PATH. Still refuse
     * settings.local.json (not covered by those flags). */
    if (mcp_exists("/.claude/settings.local.json")) {
        refuse("mcp_local");
    }
    {
        const char *why = mcp_empty_why();
        if (why) {
            refuse(why);
        }
    }
    if (!claude_dir_ok(home)) {
        refuse("claude_dir");
    }

    handoff = connect_handoff();
    if (handoff < 0) {
        refuse("handoff");
    }
    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) < 0) {
        refuse("pair");
    }
    memset(&msg, 0, sizeof msg);
    iov.iov_base = &dummy;
    iov.iov_len = 1;
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    msg.msg_control = cmsgbuf;
    msg.msg_controllen = sizeof cmsgbuf;
    cmsg = CMSG_FIRSTHDR(&msg);
    cmsg->cmsg_level = SOL_SOCKET;
    cmsg->cmsg_type = SCM_RIGHTS;
    cmsg->cmsg_len = CMSG_LEN(sizeof(int));
    memcpy(CMSG_DATA(cmsg), &pair[1], sizeof(int));
    cred.pid = getpid();
    cred.uid = getuid();
    cred.gid = getgid();
    cmsg = CMSG_NXTHDR(&msg, cmsg);
    if (cmsg) {
        cmsg->cmsg_level = SOL_SOCKET;
        cmsg->cmsg_type = SCM_CREDENTIALS;
        cmsg->cmsg_len = CMSG_LEN(sizeof cred);
        memcpy(CMSG_DATA(cmsg), &cred, sizeof cred);
    }
    if (sendmsg(handoff, &msg, 0) < 0) {
        refuse("sendmsg");
    }
    close(pair[1]);
    close(handoff);

    memset(&msg, 0, sizeof msg);
    iov.iov_base = &dummy;
    iov.iov_len = 1;
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    msg.msg_control = cmsgbuf;
    msg.msg_controllen = sizeof cmsgbuf;
    if (recvmsg(pair[0], &msg, 0) < 0) {
        refuse("recvmsg");
    }
    if (msg.msg_flags & MSG_CTRUNC) {
        close_cmsg_fds(&msg, -1);
        refuse("ctrunc");
    }
    {
        int nfds_total = 0;
        for (cmsg = CMSG_FIRSTHDR(&msg); cmsg; cmsg = CMSG_NXTHDR(&msg, cmsg)) {
            if (cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SCM_RIGHTS) {
                int nfds = (int)((cmsg->cmsg_len - CMSG_LEN(0)) / sizeof(int));
                int *fds = (int *)CMSG_DATA(cmsg);
                if (nfds != 1 || nfds_total != 0) {
                    close_cmsg_fds(&msg, -1);
                    refuse("fds");
                }
                sink = fds[0];
                nfds_total = 1;
            }
        }
    }
    if (sink < 0) {
        refuse("sink");
    }

    claude_fd = open(CSL_CLAUDE_PATH, O_RDONLY | O_CLOEXEC);
    if (claude_fd < 0) {
        refuse("claude_open");
    }
    if (!csl_ancestors_root_owned(CSL_CLAUDE_PATH)) {
        refuse("claude_own");
    }
    if (csl_sha256_fd(claude_fd, digest) != 0 || !csl_hex_eq(digest, CSL_CLAUDE_SHA256)) {
        refuse("claude_hash");
    }

#ifndef PR_SET_CHILD_SUBREAPER
#define PR_SET_CHILD_SUBREAPER 36
#endif
    if (prctl(PR_SET_CHILD_SUBREAPER, 1L) < 0) {
        refuse("subreaper");
    }

    child = fork();
    if (child < 0) {
        refuse("fork");
    }
    if (child == 0) {
        if (setpgid(0, 0) < 0) {
            _exit(1);
        }
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) < 0) {
            _exit(1);
        }
        if (getppid() == 1) {
            _exit(1);
        }
        if (dup2(sink, 2) < 0) {
            _exit(1);
        }
        if (sink != 2) {
            close(sink);
        }
        close(pair[0]);
        cargv[0] = (char *)CSL_CLAUDE_PATH;
        if (lseek(claude_fd, 0, SEEK_SET) < 0) {
            _exit(1);
        }
        fexecve(claude_fd, cargv, child_env);
        _exit(127);
    }
    close(sink);
    close(claude_fd);
    (void)setpgid(child, child);
    if (waitpid(child, &status, 0) < 0) {
        status = 1;
    }
    /* N14 / R5-L1 / C4.1: subreaper reaps orphaned descendants (including
     * setsid) outside a nested pid namespace. WNOHANG until a few
     * seconds, then SIGKILL the process group and remaining children,
     * then a final bounded WNOHANG reap. Never block forever. Writer
     * veto covers fd 2 leftovers after that; not a cgroup. */
    csl_reap_leftover_descendants(child);
    if (WIFEXITED(status)) {
        dummy = 'E';
        (void)csl_write_all(pair[0], "END\n", 4);
    }
    close(pair[0]);
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    return 1;
}
#endif
