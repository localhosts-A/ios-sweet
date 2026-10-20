/*
 * ARM64 Linux Image entry used by the Android bootloader. The Dali bootloader
 * may enter at EL1 or EL2; the MMU and D-cache are off, and x0 holds the live
 * device-tree pointer.
 */
	.section .text.head, "ax"
	.globl _head
_head:
	b	_start			/* code0 */
	.long	0			/* code1 */
	.quad	0			/* text_offset */
	.quad	_end			/* image_size (linked at 0) */
	.quad	0xa			/* flags: LE, 4K pages, place anywhere */
	.quad	0			/* res2 */
	.quad	0			/* res3 */
	.quad	0			/* res4 */
	.ascii	"ARM\x64"		/* magic */
	.long	0			/* res5 (PE offset) */

	.text
	.globl _start
_start:
	msr	daifset, #0xf
	mov	x19, x0			/* DTB pointer from ABL */
	msr	spsel, #1
	adrp	x1, _stack_top
	add	x1, x1, :lo12:_stack_top
	mov	sp, x1
	adr	x1, vectors
	#ifdef BOARD_DALI
	mrs	x2, CurrentEL
	cmp	x2, #0x8
	b.ne	1f
	msr	vbar_el2, x1
1:
	#endif
	msr	vbar_el1, x1
	isb
	/* zero .bss */
	adrp	x1, _bss_start
	add	x1, x1, :lo12:_bss_start
	adrp	x2, _bss_end
	add	x2, x2, :lo12:_bss_end
1:	cmp	x1, x2
	b.hs	2f
	str	xzr, [x1], #8
	b	1b
2:
#ifdef DALI_ANDROID_CHAIN
	/* Preserve the shim, stack and embedded stock kernel before replacing the
	 * original LK kernel buffer. This interval is the verified Dali high RAM
	 * candidate; this diagnostic never writes the XNU destination segments.
	 */
	adr	x9, _head
	mov	x3, #0x200000000
	sub	x10, x3, x9
	mov	x1, x9
	adrp	x2, _end
	add	x2, x2, :lo12:_end
6:	ldp	x4, x5, [x1], #16
	stp	x4, x5, [x3], #16
	cmp	x1, x2
	b.lo	6b
	dsb	sy
	ic	iallu
	dsb	sy
	isb
	adrp	x1, _stack_top
	add	x1, x1, :lo12:_stack_top
	add	x1, x1, x10
	mov	sp, x1
	adr	x1, vectors
	add	x1, x1, x10
	mrs	x2, CurrentEL
	cmp	x2, #8
	b.ne	7f
	msr	vbar_el2, x1
7:	msr	vbar_el1, x1
	isb
	adr	x1, relocated_start
	add	x1, x1, x10
	br	x1
#endif
relocated_start:
	mov	x0, x19
	bl	shim_main
3:	wfe
	b	3b

/* PSCI CPU_ON target. x0 = context id = cpu index (1..7). */
	.globl secondary_entry
secondary_entry:
	msr	daifset, #0xf
	msr	spsel, #1
	adrp	x1, _sec_stacks
	add	x1, x1, :lo12:_sec_stacks
	add	x1, x1, x0, lsl #12
	add	x1, x1, #0x1000
	mov	sp, x1
	adr	x1, vectors
	#ifdef BOARD_DALI
	mrs	x2, CurrentEL
	cmp	x2, #0x8
	b.ne	5f
	msr	vbar_el2, x1
5:
	#endif
	msr	vbar_el1, x1
	isb
	bl	secondary_main
4:	wfe
	b	4b

#ifdef DALI_ANDROID_CHAIN
/* Linux arm64 entry contract: x0=FDT, x1..x3=0; no return to overwritten code. */
	.globl android_chain_enter
android_chain_enter:
	mov	x4, x1
	mov	x1, xzr
	mov	x2, xzr
	mov	x3, xzr
	dsb	sy
	ic	iallu
	dsb	sy
	isb
	br	x4
#endif

/*
 * Exception vectors. Each slot stashes x29/x30, loads its slot number
 * and jumps to the common path, which saves x0-x28 and calls
 * exc_handler(slot, regs). IRQs return; everything else ends in the
 * on-screen panic.
 */
.macro vent slot
	.balign	0x80
	stp	x29, x30, [sp, #-16]!
	mov	x29, #\slot
	b	exc_common
.endm

	.balign	0x800
	.globl vectors
vectors:
	vent 0
	vent 1
	vent 2
	vent 3
	vent 4
	vent 5
	vent 6
	vent 7
	vent 8
	vent 9
	vent 10
	vent 11
	vent 12
	vent 13
	vent 14
	vent 15

exc_common:
	sub	sp, sp, #(30 * 8)
	stp	x0, x1, [sp, #0]
	stp	x2, x3, [sp, #16]
	stp	x4, x5, [sp, #32]
	stp	x6, x7, [sp, #48]
	stp	x8, x9, [sp, #64]
	stp	x10, x11, [sp, #80]
	stp	x12, x13, [sp, #96]
	stp	x14, x15, [sp, #112]
	stp	x16, x17, [sp, #128]
	stp	x18, x19, [sp, #144]
	stp	x20, x21, [sp, #160]
	stp	x22, x23, [sp, #176]
	stp	x24, x25, [sp, #192]
	stp	x26, x27, [sp, #208]
	str	x28, [sp, #224]
	mov	x0, x29
	mov	x1, sp
	bl	exc_handler
	ldp	x0, x1, [sp, #0]
	ldp	x2, x3, [sp, #16]
	ldp	x4, x5, [sp, #32]
	ldp	x6, x7, [sp, #48]
	ldp	x8, x9, [sp, #64]
	ldp	x10, x11, [sp, #80]
	ldp	x12, x13, [sp, #96]
	ldp	x14, x15, [sp, #112]
	ldp	x16, x17, [sp, #128]
	ldp	x18, x19, [sp, #144]
	ldp	x20, x21, [sp, #160]
	ldp	x22, x23, [sp, #176]
	ldp	x24, x25, [sp, #192]
	ldp	x26, x27, [sp, #208]
	ldr	x28, [sp, #224]
	add	sp, sp, #(30 * 8)
	ldp	x29, x30, [sp], #16
	eret
