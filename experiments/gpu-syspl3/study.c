// SPDX-License-Identifier: MIT
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <nvml.h>
#include <poll.h>
#include <pwd.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#ifndef STUDY_BOOT_ID
#define STUDY_BOOT_ID "not-configured"
#endif
#ifndef STUDY_USER
#define STUDY_USER "not-configured"
#endif
#ifndef BASELINE_SECONDS
#define BASELINE_SECONDS 30
#define LIMITED_SECONDS 20
#define RECOVERY_SECONDS 30
#endif
static const unsigned req = 0x118;
static volatile sig_atomic_t interrupted;
static void stop(int sig) { interrupted = sig; }
static double now(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec + t.tv_nsec * 1e-9;
}
static uint32_t rd(volatile uint32_t *m, unsigned o) { return m[o / 4]; }
static void barrier(void) { __sync_synchronize(); }
static void set_request(volatile uint32_t *m, uint32_t value) {
  m[req / 4] = value;
  barrier();
  m[0] = 1;
  barrier();
}
static int restore(volatile uint32_t *m, uint32_t owned) {
  uint32_t current = rd(m, req);
  if (!current)
    return 0;
  if (current != owned)
    return 2;
  set_request(m, 0);
  return rd(m, req) ? 3 : 0;
}
static pid_t guard(volatile uint32_t *m, uint32_t owned,
                   volatile unsigned *armed, int *notify, int timeout_ms) {
  int p[2];
  if (pipe2(p, O_CLOEXEC))
    return -1;
  pid_t child = fork();
  if (!child) {
    close(p[1]);
    signal(SIGTERM, SIG_IGN);
    signal(SIGINT, SIG_IGN);
    signal(SIGHUP, SIG_IGN);
    prctl(PR_SET_NAME, "n1x-pl3-guard");
    struct pollfd fd = {p[0], POLLIN | POLLHUP, 0};
    int r;
    do {
      r = poll(&fd, 1, timeout_ms);
    } while (r < 0 && errno == EINTR);
    _exit(*armed ? restore(m, owned) : 0);
  }
  close(p[0]);
  if (child < 0) {
    close(p[1]);
    return -1;
  }
  *notify = p[1];
  return child;
}
static int matches(const char *path, const char *expected) {
  char b[256];
  int fd = open(path, O_RDONLY | O_CLOEXEC);
  if (fd < 0)
    return 0;
  ssize_t n = read(fd, b, 255);
  close(fd);
  if (n < 1)
    return 0;
  b[n] = 0;
  b[strcspn(b, "\r\n")] = 0;
  return !strcmp(b, expected);
}
static int selftest(void) {
  volatile uint32_t *m = mmap(NULL, 4096, PROT_READ | PROT_WRITE,
                              MAP_SHARED | MAP_ANONYMOUS, -1, 0);
  if (m == MAP_FAILED)
    return 1;
  volatile unsigned *armed = mmap(NULL, 4096, PROT_READ | PROT_WRITE,
                                  MAP_SHARED | MAP_ANONYMOUS, -1, 0);
  if (armed == MAP_FAILED)
    return 1;
  int p[2];
  if (pipe(p))
    return 1;
  pid_t worker = fork();
  if (!worker) {
    close(p[0]);
    int notify;
    pid_t g = guard(m, 125000, armed, &notify, 2000);
    if (g < 0)
      _exit(2);
    *armed = 1;
    barrier();
    set_request(m, 125000);
    if (write(p[1], "R", 1) != 1)
      _exit(3);
    close(p[1]);
    for (;;)
      pause();
  }
  close(p[1]);
  char c;
  if (read(p[0], &c, 1) != 1)
    return 1;
  close(p[0]);
  kill(worker, SIGKILL);
  waitpid(worker, NULL, 0);
  double until = now() + 2;
  while (rd(m, req) && now() < until)
    usleep(10000);
  if (rd(m, req))
    return 1;
  int notify, status;
  set_request(m, 125000);
  pid_t g = guard(m, 125000, armed, &notify, 100);
  waitpid(g, &status, 0);
  close(notify);
  if (!WIFEXITED(status) || WEXITSTATUS(status) || rd(m, req))
    return 1;
  set_request(m, 120000);
  g = guard(m, 125000, armed, &notify, 100);
  waitpid(g, &status, 0);
  close(notify);
  if (!WIFEXITED(status) || WEXITSTATUS(status) != 2 || rd(m, req) != 120000)
    return 1;
  *armed = 0;
  set_request(m, 125000);
  g = guard(m, 125000, armed, &notify, 100);
  waitpid(g, &status, 0);
  close(notify);
  if (!WIFEXITED(status) || WEXITSTATUS(status) || rd(m, req) != 125000)
    return 1;
  munmap((void *)armed, 4096);
  munmap((void *)m, 4096);
  puts("PASS: parent SIGKILL, independent deadline, foreign-override "
       "ownership, disarmed guard; anonymous RAM only");
  return 0;
}
static int valid_live(volatile uint32_t *m, uint32_t owned, int applying) {
  if (!matches("/sys/firmware/acpi/platform_profile", "performance") ||
      !matches("/sys/class/power_supply/AC0/online", "1"))
    return 0;
  for (unsigned i = 0; i < 8; i++) {
    uint32_t v = rd(m, 0x100 + i * 4);
    if (i != 6 && v)
      return 0;
    if (i == 6 && v != (applying ? owned : 0))
      return 0;
  }
  if (rd(m, 0x138) < owned || rd(m, 0x758) > owned || rd(m, 0x75c) < owned)
    return 0;
  static const unsigned temps[] = {0x818, 0x820, 0x824, 0x828,
                                   0x82c, 0x830, 0x834};
  for (unsigned i = 0; i < sizeof(temps) / sizeof(temps[0]); i++) {
    uint32_t v = rd(m, temps[i]);
    if (v < 2500 || v >= 3581) {
      fprintf(stderr, "Temperature guard: offset=0x%x raw=%u C=%.2f\n",
              temps[i], v, v / 10.0 - 273.15);
      return 0;
    }
  }
  return 1;
}
static int kmsg_error(int fd) {
  if (fd < 0)
    return 0;
  char b[4096];
  ssize_t n;
  while ((n = read(fd, b, sizeof(b) - 1)) > 0) {
    b[n] = 0;
    if ((strstr(b, "NVRM") &&
         (strstr(b, "Xid") || strstr(b, "NV_ERR_") ||
          strstr(b, "Check failed") || strstr(b, "Out of memory"))) ||
        strstr(b, "GPU has fallen") || strstr(b, "GSP RPC timeout")) {
      fprintf(stderr, "GPU_KERNEL_ERROR %s\n", b);
      return 1;
    }
  }
  return 0;
}
static unsigned offsets[] = {
    0x008, 0x00c, 0x038, 0x048, 0x04c, 0x100, 0x104, 0x108, 0x10c, 0x110, 0x114,
    0x118, 0x11c, 0x120, 0x124, 0x128, 0x12c, 0x130, 0x134, 0x138, 0x13c, 0x140,
    0x144, 0x148, 0x14c, 0x150, 0x154, 0x158, 0x15c, 0x160, 0x164, 0x168, 0x16c,
    0x170, 0x174, 0x178, 0x17c, 0x20c, 0x21c, 0x22c, 0x23c, 0x24c, 0x25c, 0x300,
    0x304, 0x308, 0x30c, 0x310, 0x31c, 0x324, 0x600, 0x604, 0x620, 0x624, 0x628,
    0x62c, 0x630, 0x634, 0x708, 0x70c, 0x718, 0x71c, 0x728, 0x72c, 0x738, 0x73c,
    0x748, 0x74c, 0x758, 0x75c, 0x800, 0x804, 0x808, 0x80c, 0x810, 0x814, 0x818,
    0x81c, 0x820, 0x824, 0x828, 0x82c, 0x830, 0x834};
static void sample(FILE *f, volatile uint32_t *m, nvmlDevice_t device,
                   const char *phase, double start, int *failed) {
  unsigned clock, power;
  nvmlTemperature_t thermal = {.version = nvmlTemperature_v1,
                               .sensorType = NVML_TEMPERATURE_GPU};
  unsigned long long reasons;
  nvmlUtilization_t util;
  nvmlDevicePowerMizerModes_v1_t mode = {0};
  if (nvmlDeviceGetClockInfo(device, NVML_CLOCK_SM, &clock) ||
      nvmlDeviceGetTemperatureV(device, &thermal) ||
      nvmlDeviceGetPowerUsage(device, &power) ||
      nvmlDeviceGetUtilizationRates(device, &util) ||
      nvmlDeviceGetCurrentClocksEventReasons(device, &reasons) ||
      nvmlDeviceGetPowerMizerMode_v1(device, &mode)) {
    *failed = 1;
    return;
  }
  unsigned temp = (unsigned)thermal.temperature;
  if (temp >= 85 || mode.currentMode != 0)
    *failed = 1;
  double t = now();
  fprintf(f,
          "{\"kind\":\"sample\",\"monotonic\":%.9f,\"elapsed\":%.6f,\"phase\":"
          "\"%s\",\"sm_mhz\":%u,\"gpu_temp_c\":%u,\"gpu_power_mw\":%u,\"gpu_"
          "util\":%u,\"clock_reasons\":%llu,\"powermizer\":%u,\"words\":{",
          t, t - start, phase, clock, temp, power, util.gpu, reasons,
          mode.currentMode);
  for (unsigned i = 0; i < sizeof(offsets) / sizeof(offsets[0]); i++)
    fprintf(f, "%s\"0x%03x\":%u", i ? "," : "", offsets[i], rd(m, offsets[i]));
  fputs("}}\n", f);
  fflush(f);
}
int main(int argc, char **argv) {
  if (argc == 2 && !strcmp(argv[1], "--self-test"))
    return selftest();
  if (argc != 3 || geteuid() != 0)
    return 2;
  if (!matches("/sys/class/dmi/id/product_name", "ProArt P16 H7607BA") ||
      !matches("/sys/class/dmi/id/bios_version", "H7607BA.300") ||
      !matches("/proc/sys/kernel/random/boot_id", STUDY_BOOT_ID))
    return 2;
  signal(SIGTERM, stop);
  signal(SIGINT, stop);
  signal(SIGHUP, stop);
  setvbuf(stdout, NULL, _IOLBF, 0);
  int l1 = open("/run/lock/spark-hwmon-n1x-cap.lock",
                O_WRONLY | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600),
      l2 = open("/run/lock/n1x-capability-probe.lock",
                O_WRONLY | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
  if (l1 < 0 || l2 < 0 || flock(l1, LOCK_EX | LOCK_NB) ||
      flock(l2, LOCK_EX | LOCK_NB)) {
    perror("study lock");
    return 2;
  }
  int d = open(argv[1], O_DIRECTORY | O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
  if (d < 0)
    return 2;
  int f = openat(d, "telemetry.jsonl",
                 O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0644),
      benchlog =
          openat(d, "workload.jsonl",
                 O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0644);
  if (f < 0 || benchlog < 0) {
    perror("output");
    return 2;
  }
  FILE *log = fdopen(f, "w");
  if (!log)
    return 2;
  int mem = open("/dev/mem", O_RDWR | O_SYNC | O_CLOEXEC);
  if (mem < 0)
    return 2;
  volatile uint32_t *m =
      mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, mem, 0x1c238000);
  if (m == MAP_FAILED)
    return 2;
  uint32_t ec = rd(m, 0x138), owned = ec >= 5000 ? ec - 5000 : 0;
  if (ec < 100000 || ec > 200000 || rd(m, 0x178) != ec || rd(m, 0x158) != 0 ||
      !valid_live(m, owned, 0)) {
    fprintf(stderr, "Initial state rejected\n");
    return 2;
  }
  struct passwd *pw = getpwnam(STUDY_USER);
  if (!pw || pw->pw_uid == 0)
    return 2;
  volatile unsigned *armed = mmap(NULL, 4096, PROT_READ | PROT_WRITE,
                                  MAP_SHARED | MAP_ANONYMOUS, -1, 0);
  if (armed == MAP_FAILED)
    return 2;
#ifdef CONFIRM_RUN
  /* Wait without a workload or override for a cooler starting point. */
  double cool_deadline = now() + 45;
  unsigned cool_samples = 0;
  puts("Waiting for SoC below 76 C before the short confirmation");
  while (now() < cool_deadline && !interrupted && cool_samples < 5) {
    if (!matches("/sys/firmware/acpi/platform_profile", "performance") ||
        !matches("/sys/class/power_supply/AC0/online", "1"))
      return 2;
    cool_samples = rd(m, 0x818) < 3491 ? cool_samples + 1 : 0;
    usleep(100000);
  }
  if (cool_samples < 5 || interrupted || rd(m, 0x138) != ec ||
      !valid_live(m, owned, 0)) {
    fprintf(stderr, "No suitable initial state for confirmation\n");
    return 2;
  }
#endif
  int notify = -1;
  pid_t dog = guard(m, owned, armed, &notify, 110000);
  if (dog < 0)
    return 2;
  int result = 0, nvml_started = 0, ready[2] = {-1, -1};
  pid_t bench = -1;
  nvmlDevice_t gpu;
  char name[128], driver[96];
  int km = open("/dev/kmsg", O_RDONLY | O_NONBLOCK | O_CLOEXEC);
  if (km < 0 || lseek(km, 0, SEEK_END) < 0) {
    perror("kmsg guard");
    result = 2;
    goto done;
  }
  if (nvmlInit_v2()) {
    result = 2;
    goto done;
  }
  nvml_started = 1;
  if (nvmlDeviceGetHandleByIndex_v2(0, &gpu) ||
      nvmlDeviceGetName(gpu, name, sizeof(name)) ||
      nvmlSystemGetDriverVersion(driver, sizeof(driver))) {
    result = 2;
    goto done;
  }
  fprintf(log,
          "{\"kind\":\"metadata\",\"device\":\"%s\",\"driver\":\"%s\","
          "\"profile\":\"performance\",\"base\":\"0x1c238000\",\"request_"
          "offset\":\"0x118\",\"original_os_mw\":0,\"initial_ec_mw\":%u,\"test_"
          "request_mw\":%u,\"baseline_s\":%d,\"limited_s\":%d,\"recovered_s\":"
          "%d,\"watchdog_s\":110,\"temperature_abort_c\":85}\n",
          name, driver, ec, owned, BASELINE_SECONDS, LIMITED_SECONDS,
          RECOVERY_SECONDS);
  fflush(log);
  {
    int failed = 0;
    sample(log, m, gpu, "preflight", now(), &failed);
    if (failed) {
      result = 2;
      goto done;
    }
  }
  if (pipe2(ready, O_CLOEXEC)) {
    result = 2;
    goto done;
  }
  bench = fork();
  if (bench == 0) {
    if (initgroups(pw->pw_name, pw->pw_gid) || setgid(pw->pw_gid) ||
        setuid(pw->pw_uid))
      _exit(126);
    if (prctl(PR_SET_PDEATHSIG, SIGTERM) || getppid() == 1)
      _exit(126);
    if (dup2(benchlog, 1) < 0 || dup2(benchlog, 2) < 0 || dup2(ready[1], 3) < 0)
      _exit(126);
    fcntl(3, F_SETFD, 0);
    close_range(4, ~0U, 0);
    char duration[16];
    snprintf(duration, sizeof(duration), "%d",
             BASELINE_SECONDS + LIMITED_SECONDS + RECOVERY_SECONDS + 5);
    execl(argv[2], argv[2], "3", duration, (char *)NULL);
    _exit(127);
  }
  close(ready[1]);
  ready[1] = -1;
  close(benchlog);
  benchlog = -1;
  if (bench < 0) {
    result = 2;
    goto done;
  }
  {
    struct pollfd p = {ready[0], POLLIN | POLLHUP, 0};
    char b;
    if (poll(&p, 1, 10000) <= 0 || read(ready[0], &b, 1) != 1 || b != 'R') {
      fprintf(stderr, "Workload not ready\n");
      result = 2;
      goto done;
    }
  }
  close(ready[0]);
  ready[0] = -1;
  {
    double start = now();
    int applied = 0, restored = 0;
    puts("Baseline recording started");
    while (now() - start <
               BASELINE_SECONDS + LIMITED_SECONDS + RECOVERY_SECONDS &&
           !interrupted) {
      double elapsed = now() - start;
      if (!valid_live(m, owned, applied && !restored) || kmsg_error(km)) {
        fprintf(stderr, "State, temperature or kernel-error guard tripped\n");
        fprintf(log,
                "{\"kind\":\"abort\",\"monotonic\":%.9f,\"soc_decikelvin\":%u,"
                "\"cpu_p1_decikelvin\":%u}\n",
                now(), rd(m, 0x818), rd(m, 0x82c));
        fflush(log);
        result = 3;
        break;
      }
      if (elapsed >= BASELINE_SECONDS && !applied) {
        *armed = 1;
        barrier();
        set_request(m, owned);
        applied = 1;
        printf("SysPL3 request applied: %u mW (EC %u mW)\n", owned,
               rd(m, 0x138));
        fprintf(log,
                "{\"kind\":\"applied\",\"monotonic\":%.9f,\"request_mw\":%u}\n",
                now(), owned);
      }
      if (elapsed >= BASELINE_SECONDS + LIMITED_SECONDS && !restored) {
        if (restore(m, owned)) {
          result = 3;
          break;
        }
        restored = 1;
        puts("SysPL3 OS request restored to zero");
        fprintf(log, "{\"kind\":\"restored\",\"monotonic\":%.9f}\n", now());
      }
      if (applied && !restored && elapsed >= BASELINE_SECONDS + 1 &&
          rd(m, 0x178) != owned) {
        fprintf(stderr, "Effective SysPL3 did not acknowledge the request\n");
        result = 3;
        break;
      }
      int failed = 0;
      sample(log, m, gpu,
             restored  ? "recovered"
             : applied ? "limited"
                       : "baseline",
             start, &failed);
      if (failed) {
        fprintf(stderr, "NVML or GPU temperature guard tripped\n");
        result = 3;
        break;
      }
      int status;
      pid_t w = waitpid(bench, &status, WNOHANG);
      if (w == bench) {
        bench = -1;
        fprintf(stderr, "Workload exited early: status %d\n", status);
        result = 3;
        break;
      }
      usleep(100000);
    }
    if (interrupted)
      result = 3;
  }
done:
  {
    int reset = *armed ? restore(m, owned) : 0;
    if (reset)
      result = 4;
    if (bench > 0) {
      kill(bench, SIGTERM);
      double until = now() + 4;
      int status = 0;
      while (waitpid(bench, &status, WNOHANG) == 0 && now() < until)
        usleep(50000);
      if (now() >= until) {
        kill(bench, SIGKILL);
        waitpid(bench, &status, 0);
        result = 4;
      } else if (!WIFEXITED(status) || WEXITSTATUS(status))
        result = 4;
    }
    if (notify >= 0)
      close(notify);
    int status = 0;
    waitpid(dog, &status, 0);
    if (!WIFEXITED(status) || WEXITSTATUS(status))
      result = 4;
    double until = now() + 2;
    while (rd(m, 0x178) != rd(m, 0x138) && now() < until)
      usleep(10000);
    int clean = rd(m, req) == 0 && rd(m, 0x178) == rd(m, 0x138);
    for (unsigned i = 0; i < 8; i++)
      if (rd(m, 0x100 + i * 4))
        clean = 0;
    if (!clean)
      result = 4;
    fprintf(log,
            "{\"kind\":\"final\",\"monotonic\":%.9f,\"result\":%d,\"os_request_"
            "mw\":%u,\"effective_mw\":%u,\"ec_mw\":%u,\"clean\":%s}\n",
            now(), result, rd(m, req), rd(m, 0x178), rd(m, 0x138),
            clean ? "true" : "false");
    fflush(log);
    printf("Final request=%u effective=%u EC=%u clean=%d result=%d\n",
           rd(m, req), rd(m, 0x178), rd(m, 0x138), clean, result);
  }
  if (nvml_started)
    nvmlShutdown();
  if (km >= 0)
    close(km);
  if (ready[0] >= 0)
    close(ready[0]);
  if (ready[1] >= 0)
    close(ready[1]);
  if (benchlog >= 0)
    close(benchlog);
  fclose(log);
  munmap((void *)armed, 4096);
  munmap((void *)m, 4096);
  close(mem);
  close(d);
  close(l1);
  close(l2);
  return result;
}
