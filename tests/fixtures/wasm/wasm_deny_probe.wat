;; wasm_deny_probe.wat — TEST SOURCE ONLY. Never shipped, never run as text (METAPLAN v2 §6 R9).
;;
;; agentteams/data/wasm_deny_probe.wasm is built from this file at development time:
;;     jco parse tests/fixtures/wasm/wasm_deny_probe.wat -o agentteams/data/wasm_deny_probe.wasm
;; and tests/test_wasm_exec.py::test_deny_probe_binary_matches_source rebuilds it and compares bytes.
;;
;; Modes, chosen by the FIRST BYTE of argv[1] (no argv[1] = deny):
;;   d  deny   prints PREOPEN<0|1> ENV<0|1> SOCK<0|1>, one per line. "0" = the authority is absent.
;;             PREOPEN: fd 3 is not a preopen (fd_prestat_get -> errno 8 badf).
;;             ENV:     environ_sizes_get reports zero variables.
;;             SOCK:    sock_accept on fd 3 fails.
;;   w  write  path_open(fd 3, "probe.out", CREAT|TRUNC, fd_write) then writes "WROTE\n" to it.
;;             prints WRITE1 when the write landed, WRITE0 otherwise.
;;   s  spin   loops forever (fuel / timeout traps).
;;   g  grow   memory.grow by 2000 pages (125 MiB; trap-on-grow-failure turns refusal into a trap).
;;   f  flood  writes 4 KiB blocks of "A" to stdout until a write fails (output-cap tests).
;;   e  echo   copies stdin to stdout until EOF.
;;   x  exit   proc_exit(7).
;;   anything else: prints BADMODE and exits 2.
;;
;; Memory map (one 64 KiB page): 0..63 scratch, 64 iovec, 72 nwritten, 128.. text, 1024 argv pointers (<= 16),
;; 2048..4095 argv bytes (<= 2048), 8192..12287 I/O buffer.
(module
  (import "wasi_snapshot_preview1" "args_sizes_get" (func $args_sizes (param i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "args_get" (func $args_get (param i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "fd_prestat_get" (func $prestat (param i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "environ_sizes_get" (func $envsz (param i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "sock_accept" (func $accept (param i32 i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "path_open"
    (func $path_open (param i32 i32 i32 i32 i32 i64 i64 i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "fd_read" (func $read (param i32 i32 i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "fd_write" (func $write (param i32 i32 i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "proc_exit" (func $exit (param i32)))
  (memory (export "memory") 1)
  (data (i32.const 128) "PREOPEN0\nPREOPEN1\nENV0\nENV1\nSOCK0\nSOCK1\nWRITE0\nWRITE1\n")
  (data (i32.const 192) "probe.out")
  (data (i32.const 208) "WROTE\n")
  (data (i32.const 224) "BADMODE\n")

  ;; write len bytes at off to fd; returns the WASI errno
  (func $put (param $fd i32) (param $off i32) (param $len i32) (result i32)
    (i32.store (i32.const 64) (local.get $off))
    (i32.store (i32.const 68) (local.get $len))
    (call $write (local.get $fd) (i32.const 64) (i32.const 1) (i32.const 72)))
  (func $say (param $off i32) (param $len i32)
    (drop (call $put (i32.const 1) (local.get $off) (local.get $len))))

  ;; first byte of argv[1], or 'd' when there is none or the arguments do not fit
  (func $mode (result i32)
    (if (i32.ne (call $args_sizes (i32.const 0) (i32.const 4)) (i32.const 0)) (then (return (i32.const 100))))
    (if (i32.lt_u (i32.load (i32.const 0)) (i32.const 2)) (then (return (i32.const 100))))
    (if (i32.gt_u (i32.load (i32.const 0)) (i32.const 16)) (then (return (i32.const 100))))
    (if (i32.gt_u (i32.load (i32.const 4)) (i32.const 2048)) (then (return (i32.const 100))))
    (if (i32.ne (call $args_get (i32.const 1024) (i32.const 2048)) (i32.const 0)) (then (return (i32.const 100))))
    (i32.load8_u (i32.load (i32.const 1028))))

  (func $deny
    (if (i32.eq (call $prestat (i32.const 3) (i32.const 0)) (i32.const 8))
      (then (call $say (i32.const 128) (i32.const 9))) (else (call $say (i32.const 137) (i32.const 9))))
    (i32.store (i32.const 0) (i32.const 0))
    (drop (call $envsz (i32.const 0) (i32.const 4)))
    (if (i32.eqz (i32.load (i32.const 0)))
      (then (call $say (i32.const 146) (i32.const 5))) (else (call $say (i32.const 151) (i32.const 5))))
    (if (i32.ne (call $accept (i32.const 3) (i32.const 0) (i32.const 0)) (i32.const 0))
      (then (call $say (i32.const 156) (i32.const 6))) (else (call $say (i32.const 162) (i32.const 6)))))

  (func $write_mode
    (local $fd i32)
    ;; path_open(dirfd 3, lookupflags 0, path, len 9, oflags CREAT|TRUNC, rights fd_write, inheriting 0, fdflags 0)
    (if (i32.ne (call $path_open (i32.const 3) (i32.const 0) (i32.const 192) (i32.const 9) (i32.const 9)
                  (i64.const 64) (i64.const 0) (i32.const 0) (i32.const 32)) (i32.const 0))
      (then (call $say (i32.const 168) (i32.const 7)) (return)))
    (local.set $fd (i32.load (i32.const 32)))
    (if (i32.and (i32.eqz (call $put (local.get $fd) (i32.const 208) (i32.const 6)))
                 (i32.eq (i32.load (i32.const 72)) (i32.const 6)))
      (then (call $say (i32.const 175) (i32.const 7))) (else (call $say (i32.const 168) (i32.const 7)))))

  (func $flood
    (memory.fill (i32.const 8192) (i32.const 65) (i32.const 4096))
    (loop $l (br_if $l (i32.eqz (call $put (i32.const 1) (i32.const 8192) (i32.const 4096))))))

  (func $echo
    (loop $l
      (i32.store (i32.const 64) (i32.const 8192))
      (i32.store (i32.const 68) (i32.const 4096))
      (if (i32.ne (call $read (i32.const 0) (i32.const 64) (i32.const 1) (i32.const 72)) (i32.const 0)) (then (return)))
      (if (i32.eqz (i32.load (i32.const 72))) (then (return)))
      (if (i32.ne (call $put (i32.const 1) (i32.const 8192) (i32.load (i32.const 72))) (i32.const 0)) (then (return)))
      (br $l)))

  (func (export "_start")
    (local $m i32)
    (local.set $m (call $mode))
    (if (i32.eq (local.get $m) (i32.const 100)) (then (call $deny) (return)))          ;; d
    (if (i32.eq (local.get $m) (i32.const 119)) (then (call $write_mode) (return)))    ;; w
    (if (i32.eq (local.get $m) (i32.const 115)) (then (loop $spin (br $spin))))        ;; s
    (if (i32.eq (local.get $m) (i32.const 103))                                        ;; g
      (then (drop (memory.grow (i32.const 2000))) (return)))
    (if (i32.eq (local.get $m) (i32.const 102)) (then (call $flood) (return)))         ;; f
    (if (i32.eq (local.get $m) (i32.const 101)) (then (call $echo) (return)))          ;; e
    (if (i32.eq (local.get $m) (i32.const 120)) (then (call $exit (i32.const 7))))     ;; x
    (call $say (i32.const 224) (i32.const 8))
    (call $exit (i32.const 2))))
