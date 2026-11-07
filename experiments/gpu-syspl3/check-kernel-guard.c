// SPDX-License-Identifier: MIT
#define main study_entry
#include "study.c"
#undef main
static int check(const char *message, int expected) {
  int p[2];
  if (pipe(p))
    return 1;
  size_t n = strlen(message);
  if (write(p[1], message, n) != (ssize_t)n)
    return 1;
  close(p[1]);
  int actual = kmsg_error(p[0]);
  close(p[0]);
  return actual != expected;
}
int main(void) {
  int failed = check("NVRM: GPU0 nvCheckOkFailedNoLog: Check failed: Out of "
                     "memory [NV_ERR_NO_MEMORY]",
                     1) ||
               check("NVRM: Xid (PCI:0000:01:00): 31", 1) ||
               check("GSP RPC timeout", 1) ||
               check("docker0: entered forwarding state", 0);
  puts(failed ? "FAIL: kernel error recognition"
              : "PASS: kernel error recognition; pipes only");
  return failed;
}
