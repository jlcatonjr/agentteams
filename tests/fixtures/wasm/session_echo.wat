;; session_echo: the #567 session-mode test guest. Echoes stdin to stdout chunk by chunk until EOF, then exits 0.
;; Given any guest argument, first writes argv[0] and every argument, one per line (so a test can see argv), then echoes.
;; Rebuild: wat2wasm session_echo.wat -o session_echo.wasm (a test checks the pair when wat2wasm is present).
(module
  (import "wasi_snapshot_preview1" "fd_read" (func $fd_read (param i32 i32 i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "fd_write" (func $fd_write (param i32 i32 i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "args_sizes_get" (func $args_sizes_get (param i32 i32) (result i32)))
  (import "wasi_snapshot_preview1" "args_get" (func $args_get (param i32 i32) (result i32)))
  (memory (export "memory") 2)
  ;; 0: iovec {ptr,len}; 8: nread/nwritten; 16: argc; 20: argv buf size; 64: newline; 1024: argv ptrs; 4096: argv buf
  ;; 65536..131071: the echo buffer
  (data (i32.const 64) "\n")
  (func $write_all (param $ptr i32) (param $len i32)
    (block $done
      (loop $more
        (br_if $done (i32.eqz (local.get $len)))
        (i32.store (i32.const 0) (local.get $ptr))
        (i32.store (i32.const 4) (local.get $len))
        (br_if $done (call $fd_write (i32.const 1) (i32.const 0) (i32.const 1) (i32.const 8)))
        (local.set $ptr (i32.add (local.get $ptr) (i32.load (i32.const 8))))
        (local.set $len (i32.sub (local.get $len) (i32.load (i32.const 8))))
        (br $more))))
  (func $strlen (param $p i32) (result i32) (local $n i32)
    (block $done (loop $more
      (br_if $done (i32.eqz (i32.load8_u (i32.add (local.get $p) (local.get $n)))))
      (local.set $n (i32.add (local.get $n) (i32.const 1)))
      (br $more)))
    (local.get $n))
  (func (export "_start") (local $i i32) (local $argc i32) (local $p i32) (local $n i32)
    ;; argv (argc includes argv[0]); bounded to what fits below the echo buffer
    (if (i32.eqz (call $args_sizes_get (i32.const 16) (i32.const 20)))
      (then
        (local.set $argc (i32.load (i32.const 16)))
        (if (i32.and (i32.and (i32.gt_u (local.get $argc) (i32.const 1)) (i32.le_u (local.get $argc) (i32.const 700))) (i32.le_u (i32.load (i32.const 20)) (i32.const 61000)))
          (then
            (drop (call $args_get (i32.const 1024) (i32.const 4096)))
            (local.set $i (i32.const 0))
            (block $done (loop $more
              (br_if $done (i32.ge_u (local.get $i) (local.get $argc)))
              (local.set $p (i32.load (i32.add (i32.const 1024) (i32.mul (local.get $i) (i32.const 4)))))
              (call $write_all (local.get $p) (call $strlen (local.get $p)))
              (call $write_all (i32.const 64) (i32.const 1))
              (local.set $i (i32.add (local.get $i) (i32.const 1)))
              (br $more)))))))
    ;; echo
    (block $eof (loop $more
      (i32.store (i32.const 0) (i32.const 65536))
      (i32.store (i32.const 4) (i32.const 65536))
      (br_if $eof (call $fd_read (i32.const 0) (i32.const 0) (i32.const 1) (i32.const 8)))
      (local.set $n (i32.load (i32.const 8)))
      (br_if $eof (i32.eqz (local.get $n)))
      (call $write_all (i32.const 65536) (local.get $n))
      (br $more)))))
