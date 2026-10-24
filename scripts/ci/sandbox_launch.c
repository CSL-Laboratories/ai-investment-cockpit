/* D10 E3-2 static launcher. Runner-uid. Not in sudoers. */
#include "sandbox_argv.h"

#include <dirent.h>
#include <limits.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>

extern char **environ;

static void refuse(const char *why)
{
    (void)why;
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
    if (getenv("RUNNER_TEMP")) {
        ADD("RUNNER_TEMP", getenv("RUNNER_TEMP"));
    }
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

int main(int argc, char **argv)
{
    char home[256], user[64];
    char **child_env;
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

    if (getenv("LD_PRELOAD") || getenv("LD_AUDIT")) {
        refuse("ld");
    }
    if (u15_refuse()) {
        refuse("u15");
    }
    if (csl_argv_allow(argc, argv) != 0) {
        refuse("bad_argv");
    }
    if (csl_lookup_user(getuid(), user, sizeof user, home, sizeof home) != 0) {
        refuse("passwd");
    }
    if (prctl(PR_SET_DUMPABLE, 0) < 0) {
        refuse("dumpable");
    }
    child_env = build_child_env(home, user);

    /* F6: restored tree must not have cwd .mcp.json / .claude/settings*. */
    if (mcp_exists("/.mcp.json") || mcp_exists("/.claude/settings.json") ||
        mcp_exists("/.claude/settings.local.json")) {
        /* DR-4: fail closed. Honest runs fail until base drops these. */
        refuse("mcp");
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
    for (cmsg = CMSG_FIRSTHDR(&msg); cmsg; cmsg = CMSG_NXTHDR(&msg, cmsg)) {
        if (cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SCM_RIGHTS) {
            memcpy(&sink, CMSG_DATA(cmsg), sizeof sink);
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

    child = fork();
    if (child < 0) {
        refuse("fork");
    }
    if (child == 0) {
        char *cargv[64];
        int i;
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
        for (i = 1; i < argc && i < 62; i++) {
            cargv[i] = argv[i];
        }
        cargv[i] = NULL;
        if (lseek(claude_fd, 0, SEEK_SET) < 0) {
            _exit(1);
        }
        fexecve(claude_fd, cargv, child_env);
        _exit(127);
    }
    close(sink);
    close(claude_fd);
    if (waitpid(child, &status, 0) < 0) {
        status = 1;
    }
    dummy = 'E';
    (void)csl_write_all(pair[0], "END\n", 4);
    close(pair[0]);
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    return 1;
}
