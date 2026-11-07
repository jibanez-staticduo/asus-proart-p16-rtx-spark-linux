// SPDX-License-Identifier: MIT
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cublas_v2.h>
#include <cuda_fp16.h>
#include <cuda_runtime.h>
#include <time.h>
#include <unistd.h>
static volatile sig_atomic_t stopped;
static void stop(int) { stopped = 1; }
static double now() {
  timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec + t.tv_nsec * 1e-9;
}
#define CUDA(x)                                                                \
  do {                                                                         \
    auto e = (x);                                                              \
    if (e != cudaSuccess) {                                                    \
      fprintf(stderr, "CUDA %s: %s\n", #x, cudaGetErrorString(e));             \
      return 1;                                                                \
    }                                                                          \
  } while (0)
#define BLAS(x)                                                                \
  do {                                                                         \
    auto e = (x);                                                              \
    if (e != CUBLAS_STATUS_SUCCESS) {                                          \
      fprintf(stderr, "cuBLAS %s: %d\n", #x, int(e));                          \
      return 1;                                                                \
    }                                                                          \
  } while (0)
__global__ void fill(__half *a, __half *b, size_t count) {
  for (size_t i = blockIdx.x * blockDim.x + threadIdx.x; i < count;
       i += blockDim.x * gridDim.x) {
    a[i] = __float2half(1.f / 128);
    b[i] = __float2half(1.f / 256);
  }
}
int main(int argc, char **argv) {
  if (argc != 3 || geteuid() == 0)
    return 2;
  int ready = atoi(argv[1]);
  double duration = atof(argv[2]);
  if (ready != 3 || duration < 1 || duration > 100)
    return 2;
  signal(SIGINT, stop);
  signal(SIGTERM, stop);
  signal(SIGHUP, stop);
  setvbuf(stdout, nullptr, _IOLBF, 0);
  constexpr int n = 8192;
  constexpr size_t count = size_t(n) * n;
  __half *a, *b, *c;
  cudaStream_t stream;
  cudaEvent_t begin, end;
  cublasHandle_t h;
  CUDA(cudaStreamCreateWithFlags(&stream, cudaStreamNonBlocking));
  CUDA(cudaMalloc(&a, count * 2));
  CUDA(cudaMalloc(&b, count * 2));
  CUDA(cudaMalloc(&c, count * 2));
  fill<<<1024, 256, 0, stream>>>(a, b, count);
  CUDA(cudaGetLastError());
  BLAS(cublasCreate(&h));
  BLAS(cublasSetStream(h, stream));
  float alpha = 1, beta = 0;
  auto gemm = [&]() {
    return cublasGemmEx(h, CUBLAS_OP_N, CUBLAS_OP_N, n, n, n, &alpha, a,
                        CUDA_R_16F, n, b, CUDA_R_16F, n, &beta, c, CUDA_R_16F,
                        n, CUBLAS_COMPUTE_32F, CUBLAS_GEMM_DEFAULT_TENSOR_OP);
  };
  for (int i = 0; i < 3; i++)
    BLAS(gemm());
  CUDA(cudaStreamSynchronize(stream));
  CUDA(cudaEventCreate(&begin));
  CUDA(cudaEventCreate(&end));
  double start = now();
  printf(
      "{\"kind\":\"workload\",\"monotonic\":%.9f,\"n\":8192,\"dtype\":\"fp16\","
      "\"accumulate\":\"fp32\",\"allocation_bytes\":402653184,\"uid\":%u}\n",
      start, geteuid());
  if (write(ready, "R", 1) != 1)
    return 2;
  close(ready);
  while (!stopped && now() - start < duration) {
    double wall_start = now();
    float ms = 0;
    unsigned iters = 0;
    CUDA(cudaEventRecord(begin, stream));
    do {
      for (int j = 0; j < 4; j++) {
        BLAS(gemm());
        iters++;
      }
      CUDA(cudaEventRecord(end, stream));
      CUDA(cudaEventSynchronize(end));
      CUDA(cudaEventElapsedTime(&ms, begin, end));
    } while (!stopped && now() - wall_start < 0.5);
    __half values[8];
    for (int j = 0; j < 8; j++)
      CUDA(cudaMemcpy(values + j, c + (count - 1) * j / 7, 2,
                      cudaMemcpyDeviceToHost));
    bool valid = true;
    for (auto v : values)
      valid = valid && std::fabs(__half2float(v) - .25f) < .0001f;
    double stop_time = now();
    printf("{\"kind\":\"window\",\"start\":%.9f,\"end\":%.9f,\"iterations\":%u,"
           "\"gpu_ms\":%.3f,\"wall_seconds\":%.6f,\"gpu_tflops\":%.5f,\"wall_"
           "tflops\":%.5f,\"correct\":%s}\n",
           wall_start, stop_time, iters, ms, stop_time - wall_start,
           2.0 * n * n * n * iters / (ms * 1e9),
           2.0 * n * n * n * iters / ((stop_time - wall_start) * 1e12),
           valid ? "true" : "false");
    if (!valid)
      return 3;
  }
  BLAS(cublasDestroy(h));
  CUDA(cudaEventDestroy(begin));
  CUDA(cudaEventDestroy(end));
  CUDA(cudaFree(a));
  CUDA(cudaFree(b));
  CUDA(cudaFree(c));
  CUDA(cudaStreamDestroy(stream));
  printf("{\"kind\":\"workload_exit\",\"monotonic\":%.9f,\"stopped\":%s}\n",
         now(), stopped ? "true" : "false");
  return 0;
}
