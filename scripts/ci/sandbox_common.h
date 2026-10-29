/* CSL-474 D10 shared constants and helpers for static launch/capture/relay.
 * Production paths are compiled in. Tests may define CSL_PREFIX. */
#ifndef CSL_SANDBOX_COMMON_H
#define CSL_SANDBOX_COMMON_H

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#ifdef CSL_PREFIX
#define CSL_STATE_DIR CSL_PREFIX "/state"
#define CSL_LAUNCH_PATH CSL_PREFIX "/bin/launch"
#define CSL_RELAY_PATH CSL_PREFIX "/bin/relay"
#define CSL_CAPTURE_START_PATH CSL_PREFIX "/bin/capture-start"
#define CSL_FILTER_PATH CSL_PREFIX "/bin/filter"
#define CSL_CLAUDE_PATH CSL_PREFIX "/claude"
#define CSL_BUN_PATH CSL_PREFIX "/bun"
#else
#define CSL_STATE_DIR "/var/lib/csl-relay"
#define CSL_LAUNCH_PATH "/opt/csl-claude/launch"
#define CSL_RELAY_PATH "/opt/csl-relay/bin/relay"
#define CSL_CAPTURE_START_PATH "/opt/csl-relay/bin/capture-start"
#define CSL_FILTER_PATH "/opt/csl-relay/bin/filter"
#define CSL_CLAUDE_PATH "/opt/csl-claude/2.1.306/claude"
#define CSL_BUN_PATH "/opt/csl-bun/bun"
#endif

#define CSL_FIFO_PATH CSL_STATE_DIR "/stderr.fifo"
#define CSL_CAPTURE_PATH CSL_STATE_DIR "/capture"
#define CSL_HANDOFF_SOCK CSL_STATE_DIR "/handoff.sock"
#define CSL_CONTROL_SOCK CSL_STATE_DIR "/control.sock"
#define CSL_END_PATH CSL_STATE_DIR "/end"
#define CSL_E3_RECORD_PATH CSL_STATE_DIR "/e3.record"
#define CSL_ALREADY_PATH CSL_STATE_DIR "/already_ran"
#define CSL_PAIR_PATH CSL_STATE_DIR "/pair.closed"
#define CSL_WORKER_PID_PATH CSL_STATE_DIR "/worker.pid"
#define CSL_WORKSPACE_PATH CSL_STATE_DIR "/workspace"
#define CSL_LAUNCH_SHA_PATH CSL_STATE_DIR "/launch.sha256"
#define CSL_BUN_TOFU_PATH CSL_STATE_DIR "/bun.tofu"
#define CSL_REFUSE_PATH CSL_STATE_DIR "/launch.refuse"
#define CSL_F1_MISS_PATH CSL_STATE_DIR "/f1.miss"
#define CSL_WORKSPACE_DEFAULT "/home/runner/work/ai-investment-cockpit/ai-investment-cockpit"
#define CSL_ACTION_DIR "/home/runner/work/_actions/anthropics/claude-code-action/a5a423f75c251dcbff8a74e99f83b2cb03c72bfa"
#ifdef CSL_PREFIX
#define CSL_RUNNER_TEMP CSL_PREFIX "/tmp"
#define CSL_MCP_EMPTY_PATH CSL_PREFIX "/mcp.empty.json"
#else
#define CSL_RUNNER_TEMP "/home/runner/work/_temp"
#define CSL_MCP_EMPTY_PATH "/opt/csl-claude/mcp.empty.json"
#endif
#define CSL_MCP_EMPTY_BYTES "{\"mcpServers\":{}}\n"

#define CSL_CLAUDE_SHA256 "520bc6c1f40af466bd03a278b5ae1af1a2938bc7de5b9e5cb15526da20d71cfe"
#define CSL_CAP_BYTES (1024 * 1024)
#define CSL_MAX_LINE 512
#define CSL_WRITER_WAIT_MS 2000

#ifndef SO_PEERPIDFD
#define SO_PEERPIDFD 38
#endif
#ifndef SCM_PIDFD
#define SCM_PIDFD 0x04
#endif

#ifndef __NR_pidfd_open
#define __NR_pidfd_open 434
#endif

void csl_die(int code, const char *reason);
int csl_read_all(int fd, unsigned char *buf, size_t cap, size_t *out);
int csl_write_all(int fd, const void *buf, size_t n);
int csl_sha256_fd(int fd, unsigned char out[32]);
int csl_sha256_file(const char *path, unsigned char out[32]);
void csl_hex32(const unsigned char in[32], char out[65]);
int csl_hex_eq(const unsigned char digest[32], const char *hex64);
int csl_lookup_user(uid_t uid, char *name, size_t nlen, char *home, size_t hlen);
int csl_lookup_name(const char *name, uid_t *uid);
int csl_pidfd_open(pid_t pid, unsigned int flags);
int csl_read_pidfd_pid(int pidfd, pid_t *out);
int csl_proc_exe(pid_t pid, char *buf, size_t len);
pid_t csl_proc_ppid(pid_t pid);
int csl_proc_cmdline(pid_t pid, char *buf, size_t len, size_t *nuls);
int csl_e3_record_ok(const char *text, size_t n);
int csl_e3_set_handoff(char *text, size_t cap, size_t *n, unsigned count, int conflict);
int csl_mkdir_p(const char *path, mode_t mode);
int csl_write_file(const char *path, const void *data, size_t n, mode_t mode);
int csl_read_file(const char *path, char *buf, size_t cap, size_t *n);
int csl_path_root_owned(const char *path);
int csl_ancestors_root_owned(const char *path);
int csl_truthy(const char *value);
int csl_env_present(const char *name);
const char *csl_env_get(const char *name);
int csl_token_copy(const char *in, char *out, size_t cap);
int csl_token_read(const char *path, char *out, size_t cap);

typedef struct {
    uint32_t state[8];
    uint64_t bitcount;
    unsigned char buffer[64];
} csl_sha256_ctx;

void csl_sha256_init(csl_sha256_ctx *ctx);
void csl_sha256_update(csl_sha256_ctx *ctx, const void *data, size_t len);
void csl_sha256_final(csl_sha256_ctx *ctx, unsigned char out[32]);

#endif
