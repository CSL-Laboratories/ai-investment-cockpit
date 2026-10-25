/* CSL-474 C4.2 / P9 Probe 1: compile against sandbox_launch.c cleanup. */
#define _GNU_SOURCE
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef PR_SET_CHILD_SUBREAPER
#define PR_SET_CHILD_SUBREAPER 36
#endif

void csl_reap_leftover_descendants(pid_t child);

int main(void)
{
    int pfd[2];
    pid_t child;
    pid_t grandchild = 0;
    char buf[32];
    ssize_t n;
    int st = 0;
    long g;

    if (prctl(PR_SET_CHILD_SUBREAPER, 1L) < 0) {
        return 10;
    }
    if (pipe(pfd) < 0) {
        return 11;
    }
    child = fork();
    if (child < 0) {
        return 12;
    }
    if (child == 0) {
        pid_t mid;
        close(pfd[0]);
        mid = fork();
        if (mid < 0) {
            _exit(13);
        }
        if (mid == 0) {
            pid_t gpid = fork();
            if (gpid < 0) {
                _exit(14);
            }
            if (gpid == 0) {
                if (setsid() < 0) {
                    _exit(15);
                }
                (void)signal(SIGTERM, SIG_IGN);
                close(pfd[1]);
                for (;;) {
                    pause();
                }
            }
            {
                char out[32];
                int len = snprintf(out, sizeof out, "%ld\n", (long)gpid);
                if (len > 0 && write(pfd[1], out, (size_t)len) != (ssize_t)len) {
                    _exit(22);
                }
            }
            close(pfd[1]);
            _exit(0);
        }
        close(pfd[1]);
        _exit(0);
    }
    close(pfd[1]);
    n = read(pfd[0], buf, sizeof buf - 1);
    close(pfd[0]);
    if (n <= 0) {
        (void)waitpid(child, &st, 0);
        return 16;
    }
    buf[n] = 0;
    g = strtol(buf, NULL, 10);
    if (g <= 1) {
        (void)waitpid(child, &st, 0);
        return 17;
    }
    grandchild = (pid_t)g;
    if (waitpid(child, &st, 0) < 0) {
        return 18;
    }
    if (kill(grandchild, 0) != 0) {
        return 19;
    }
    csl_reap_leftover_descendants(child);
    if (kill(grandchild, 0) == 0) {
        (void)kill(grandchild, SIGKILL);
        (void)waitpid(grandchild, &st, WNOHANG);
        return 20;
    }
    if (errno != ESRCH) {
        return 21;
    }
    return 0;
}
