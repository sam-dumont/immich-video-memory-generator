//#region node_modules/svelte/src/internal/disclose-version.js
typeof window < "u" && ((window.__svelte ??= {}).v ??= /* @__PURE__ */ new Set()).add("5");
//#endregion
//#region node_modules/svelte/src/constants.js
var e = {}, t = Symbol("uninitialized"), n = "http://www.w3.org/1999/xhtml", r = Array.isArray, i = Array.prototype.indexOf, a = Array.prototype.includes, o = Array.from, s = Object.keys, c = Object.defineProperty, l = Object.getOwnPropertyDescriptor, u = Object.getOwnPropertyDescriptors, d = Object.prototype, f = Array.prototype, p = Object.getPrototypeOf, m = Object.isExtensible, h = () => {};
function g(e) {
	for (var t = 0; t < e.length; t++) e[t]();
}
function _() {
	var e, t;
	return {
		promise: new Promise((n, r) => {
			e = n, t = r;
		}),
		resolve: e,
		reject: t
	};
}
var v = 1024, y = 2048, b = 4096, x = 8192, S = 16384, ee = 32768, C = 1 << 25, te = 65536, w = 1 << 19, ne = 1 << 20, re = 1 << 25, ie = 1 << 21, ae = 1 << 22, oe = 1 << 23, T = Symbol("$state"), se = Symbol("component"), ce = Symbol("legacy props"), le = Symbol(""), ue = Symbol("attributes"), de = Symbol("class"), fe = Symbol("style"), pe = Symbol("text"), me = Symbol("form reset"), he = new class extends Error {
	name = "StaleReactionError";
	message = "The reaction that called `getAbortSignal()` was re-run or destroyed";
}(), ge = !!globalThis.document?.contentType && /* @__PURE__ */ globalThis.document.contentType.includes("xml");
function _e() {
	console.warn("https://svelte.dev/e/derived_inert");
}
function ve(e) {
	console.warn("https://svelte.dev/e/hydration_mismatch");
}
function ye() {
	console.warn("https://svelte.dev/e/select_multiple_invalid_value");
}
function be() {
	console.warn("https://svelte.dev/e/svelte_boundary_reset_noop");
}
//#endregion
//#region node_modules/svelte/src/internal/client/dom/hydration.js
var E = !1;
function xe(e) {
	E = e;
}
var D;
function O(t) {
	if (t === null) throw ve(), e;
	return D = t;
}
function Se() {
	return O(/* @__PURE__ */ Zt(D));
}
function k(t) {
	if (E) {
		if (/* @__PURE__ */ Zt(D) !== null) throw ve(), e;
		D = t;
	}
}
function Ce(e = 1) {
	if (E) {
		for (var t = e, n = D; t--;) n = /* @__PURE__ */ Zt(n);
		D = n;
	}
}
function we(e = !0) {
	for (var t = 0, n = D;;) {
		if (n.nodeType === 8) {
			var r = n.data;
			if (r === "]") {
				if (t === 0) return n;
				--t;
			} else (r === "[" || r === "[!" || r[0] === "[" && !isNaN(Number(r.slice(1)))) && (t += 1);
		}
		var i = /* @__PURE__ */ Zt(n);
		e && n.remove(), n = i;
	}
}
function Te(t) {
	if (!t || t.nodeType !== 8) throw ve(), e;
	return t.data;
}
//#endregion
//#region node_modules/svelte/src/internal/client/reactivity/equality.js
function Ee(e) {
	return e === this.v;
}
function De(e, t) {
	return e == e ? e !== t || typeof e == "object" && !!e || typeof e == "function" : t == t;
}
function Oe(e) {
	return !De(e, this.v);
}
//#endregion
//#region node_modules/svelte/src/internal/client/errors.js
function ke() {
	throw Error("https://svelte.dev/e/async_derived_orphan");
}
function Ae(e, t, n) {
	throw Error("https://svelte.dev/e/each_key_duplicate");
}
function je() {
	throw Error("https://svelte.dev/e/effect_update_depth_exceeded");
}
function Me() {
	throw Error("https://svelte.dev/e/hydration_failed");
}
function Ne(e) {
	throw Error("https://svelte.dev/e/props_invalid_value");
}
function Pe() {
	throw Error("https://svelte.dev/e/state_descriptors_fixed");
}
function Fe() {
	throw Error("https://svelte.dev/e/state_prototype_fixed");
}
function Ie() {
	throw Error("https://svelte.dev/e/state_unsafe_mutation");
}
function Le() {
	throw Error("https://svelte.dev/e/svelte_boundary_reset_onerror");
}
//#endregion
//#region node_modules/svelte/src/internal/client/context.js
var A = null;
function Re(e) {
	A = e;
}
function ze(e, t = !1, n) {
	A = {
		p: A,
		i: !1,
		c: null,
		e: null,
		s: e,
		x: null,
		r: K,
		l: null
	};
}
function Be(e) {
	var t = A, n = t.e;
	if (n !== null) {
		t.e = null;
		for (var r of n) un(r);
	}
	return e !== void 0 && (t.x = e), t.i = !0, A = t.p, Ve(e);
}
function Ve(e = {}) {
	return c(e, se, { value: !0 }), e;
}
function He() {
	return !0;
}
//#endregion
//#region node_modules/svelte/src/internal/client/dom/task.js
var Ue = [];
function We() {
	var e = Ue;
	Ue = [], g(e);
}
function Ge(e) {
	if (Ue.length === 0 && !_t) {
		var t = Ue;
		queueMicrotask(() => {
			t === Ue && We();
		});
	}
	Ue.push(e);
}
function Ke() {
	for (; Ue.length > 0;) We();
}
//#endregion
//#region node_modules/svelte/src/internal/client/reactivity/status.js
var qe = ~(y | b | v);
function j(e, t) {
	e.f = e.f & qe | t;
}
function Je(e) {
	e.f & 512 || e.deps === null ? j(e, v) : j(e, b);
}
//#endregion
//#region node_modules/svelte/src/internal/client/reactivity/utils.js
function Ye(e, t, n) {
	e.f & 2048 ? t.add(e) : e.f & 4096 && n.add(e), j(e, v);
}
//#endregion
//#region node_modules/svelte/src/internal/client/dom/elements/misc.js
var Xe = !1;
function Ze() {
	Xe || (Xe = !0, document.addEventListener("reset", (e) => {
		Promise.resolve().then(() => {
			if (!e.defaultPrevented) for (let t of e.target.elements) t[me]?.();
		});
	}, { capture: !0 }));
}
//#endregion
//#region node_modules/svelte/src/internal/client/dom/elements/bindings/shared.js
function Qe(e) {
	var t = U, n = K;
	G(null), jn(null);
	try {
		return e();
	} finally {
		G(t), jn(n);
	}
}
function $e(e, t, n, r = n) {
	e.addEventListener(t, () => Qe(n));
	let i = e[me];
	e[me] = i ? () => {
		i(), r(!0);
	} : () => r(!0), Ze();
}
//#endregion
//#region node_modules/svelte/src/internal/client/reactivity/async.js
function et(e, t, n, r) {
	let i = He() ? it : ct;
	var a = e.filter((e) => !e.settled), o = t.map(i);
	if (n.length === 0 && a.length === 0) {
		r(o);
		return;
	}
	var s = K, c = tt(), l = a.length === 1 ? a[0].promise : a.length > 1 ? Promise.all(a.map((e) => e.promise)) : null;
	function u(e) {
		if (!(s.f & 16384)) {
			c();
			try {
				r([...o, ...e]);
			} catch (e) {
				an(e, s);
			}
			nt();
		}
	}
	var d = rt();
	if (n.length === 0) {
		l.then(() => u([])).finally(d);
		return;
	}
	function f() {
		Promise.all(n.map((e) => /* @__PURE__ */ ot(e))).then(u).catch((e) => an(e, s)).finally(d);
	}
	l ? l.then(() => {
		c(), f(), nt();
	}) : f();
}
function tt() {
	var e = K, t = U, n = A, r = M;
	return function(i = !0) {
		jn(e), G(t), Re(n), i && !(e.f & 16384) && (r?.activate(), r?.apply());
	};
}
function nt(e = !0) {
	jn(null), G(null), Re(null), e && M?.deactivate();
}
function rt() {
	var e = K, t = e.b, n = M, r = !!t?.is_rendered();
	return t?.update_pending_count(1, n), n.increment(r, e), () => {
		t?.update_pending_count(-1, n), n.decrement(r, e);
	};
}
/*#__NO_SIDE_EFFECTS__*/
function it(e) {
	var n = 2 | y;
	return K !== null && (K.f |= w), {
		ctx: A,
		deps: null,
		effects: null,
		equals: Ee,
		f: n,
		fn: e,
		reactions: null,
		rv: 0,
		v: t,
		wv: 0,
		parent: K,
		ac: null
	};
}
var at = Symbol("obsolete");
/*#__NO_SIDE_EFFECTS__*/
function ot(e, n, r) {
	let i = K;
	i === null && ke();
	var a = void 0, o = Pt(t), s = !U, c = /* @__PURE__ */ new Set();
	return mn(() => {
		var t = K, n = _();
		a = n.promise;
		try {
			Promise.resolve(e()).then(n.resolve, (e) => {
				e !== he && n.reject(e);
			}).finally(nt);
		} catch (e) {
			n.reject(e), nt();
		}
		var r = M;
		if (s) {
			if (t.f & 32768) var l = rt();
			if (i.b?.is_rendered()) r.async_deriveds.get(t)?.reject(at);
			else for (let e of c.values()) e.reject(at);
			c.add(n), r.async_deriveds.set(t, n);
		}
		let u = (e, t = void 0) => {
			l?.(), c.delete(n), t !== at && (r.activate(), t ? (o.f |= oe, Rt(o, t)) : (o.f & 8388608 && (o.f ^= oe), Rt(o, e)), r.deactivate());
		};
		n.promise.then(u, (e) => u(null, e || "unknown"));
	}), ln(() => {
		for (let e of c) e.reject(at);
	}), new Promise((e) => {
		function t(n) {
			function r() {
				n === a ? e(o) : t(a);
			}
			n.then(r, r);
		}
		t(a);
	});
}
/*#__NO_SIDE_EFFECTS__*/
function st(e) {
	let t = /* @__PURE__ */ it(e);
	return Nn(t), t;
}
/*#__NO_SIDE_EFFECTS__*/
function ct(e) {
	let t = /* @__PURE__ */ it(e);
	return t.equals = Oe, t;
}
function lt(e) {
	var t = e.effects;
	if (t !== null) {
		e.effects = null;
		for (var n = 0; n < t.length; n += 1) H(t[n]);
	}
}
function ut(e) {
	var n, r = K, i = e.parent;
	if (!kn && i !== null && e.v !== t && i.f & 24576) return _e(), e.v;
	jn(i);
	try {
		lt(e), n = Hn(e);
	} finally {
		jn(r);
	}
	return n;
}
function dt(e) {
	var t = ut(e);
	if (!e.equals(t) && (e.wv = zn(), (!M?.is_fork || e.deps === null) && (M === null ? e.v = t : (M.capture(e, t, !0), ht?.capture(e, t, !0)), e.deps === null))) {
		j(e, v);
		return;
	}
	kn || (N === null ? Je(e) : (cn() || M?.is_fork) && N.set(e, t));
}
function ft(e) {
	if (e.effects !== null) for (let t of e.effects) (t.teardown || t.ac) && (t.teardown?.(), t.ac !== null && Qe(() => {
		t.ac.abort(he), t.ac = null;
	}), t.fn !== null && (t.teardown = h), Gn(t, 0), vn(t));
}
function pt(e) {
	if (e.effects !== null) for (let t of e.effects) t.teardown && t.fn !== null && Kn(t);
}
//#endregion
//#region node_modules/svelte/src/internal/client/reactivity/batch.js
var mt = null, M = null, ht = null, N = null, gt = null, _t = !1, vt = !1, yt = null, bt = null, xt = 0, St = 1, Ct = class e {
	id = St++;
	#e = !1;
	linked = !0;
	#t = null;
	#n = null;
	async_deriveds = /* @__PURE__ */ new Map();
	current = /* @__PURE__ */ new Map();
	previous = /* @__PURE__ */ new Map();
	#r = /* @__PURE__ */ new Set();
	#i = /* @__PURE__ */ new Set();
	#a = 0;
	#o = /* @__PURE__ */ new Map();
	#s = null;
	#c = [];
	#l = [];
	#u = /* @__PURE__ */ new Set();
	#d = /* @__PURE__ */ new Set();
	#f = /* @__PURE__ */ new Map();
	#p = /* @__PURE__ */ new Set();
	is_fork = !1;
	#m = !1;
	constructor() {
		mt === null ? mt = this : (mt.#n = this, this.#t = mt), mt = this;
	}
	#h() {
		if (this.is_fork) return !0;
		for (let n of this.#o.keys()) {
			for (var e = n, t = !1; e.parent !== null;) {
				if (this.#f.has(e)) {
					t = !0;
					break;
				}
				e = e.parent;
			}
			if (!t) return !0;
		}
		return !1;
	}
	skip_effect(e) {
		this.#f.has(e) || this.#f.set(e, {
			d: [],
			m: []
		}), this.#p.delete(e);
	}
	unskip_effect(e, t = (e) => this.schedule(e)) {
		var n = this.#f.get(e);
		if (n) {
			this.#f.delete(e);
			for (var r of n.d) j(r, y), t(r);
			for (r of n.m) j(r, b), t(r);
		}
		this.#p.add(e);
	}
	#g() {
		var e = [];
		for (let i of this.#c) if (!(i.f & 16384 || !(i.f & 6144))) {
			for (var t = i, n = !1; t.parent !== null;) {
				t = t.parent;
				var r = t.f;
				if (r & 96) {
					if (!(r & 1024)) {
						n = !0;
						break;
					}
					t.f ^= v;
				}
			}
			n || e.push(t);
		}
		return this.#c = [], e;
	}
	#_() {
		this.#e = !0;
		for (let e of this.#u) this.#d.delete(e), j(e, y), this.schedule(e);
		for (let e of this.#d) j(e, b), this.schedule(e);
		this.apply();
		for (var t = yt = [], n = [], r = bt = []; this.#c.length > 0;) {
			xt++ > 1e3 && (this.#S(), Tt());
			for (let e of this.#g()) try {
				this.#v(e, t, n);
			} catch (t) {
				throw At(e), this.#h() || this.discard(), t;
			}
		}
		if (M = null, r.length > 0) {
			var i = e.ensure();
			for (let e of r) i.schedule(e);
		}
		if (yt = null, bt = null, this.#h()) {
			this.#x(n), this.#x(t);
			for (let [e, t] of this.#f) kt(e, t);
			r.length > 0 && M.#_();
			return;
		}
		let a = this.#y();
		if (a) {
			this.#x(n), this.#x(t), a.#b(this);
			return;
		}
		this.#u.clear(), this.#d.clear();
		for (let e of this.#r) e(this);
		this.#r.clear(), ht = this, Dt(n), Dt(t), ht = null, this.#s?.resolve();
		var o = M;
		if (this.#a === 0 && (this.#c.length === 0 || o !== null) && this.#S(), this.#c.length > 0) {
			if (o !== null) {
				for (let e of this.#c) o.#c.push(e);
				this.#c = [];
			} else o = this;
		}
		o !== null && (Mt.clear(), o.#_());
	}
	#v(e, t, n) {
		e.f ^= v;
		for (var r = e.first; r !== null;) {
			var i = r.f, a = !!(i & 96);
			if (!(a && i & 1024 || i & 8192 || this.#f.has(r)) && r.fn !== null) {
				a ? r.f ^= v : i & 4 ? t.push(r) : Bn(r) && (i & 16 && this.#d.add(r), Kn(r));
				var o = r.first;
				if (o !== null) {
					r = o;
					continue;
				}
			}
			for (; r !== null;) {
				var s = r.next;
				if (s !== null) {
					r = s;
					break;
				}
				r = r.parent;
			}
		}
	}
	#y() {
		for (var e = this.#t; e !== null;) {
			if (!e.is_fork) {
				for (let [t, [, n]] of this.current) if (e.current.has(t) && !n) return e;
			}
			e = e.#t;
		}
		return null;
	}
	#b(e) {
		for (let [t, n] of e.current) !this.previous.has(t) && e.previous.has(t) && this.previous.set(t, e.previous.get(t)), this.current.set(t, n);
		for (let [t, n] of e.async_deriveds) {
			let e = this.async_deriveds.get(t);
			e && n.promise.then(e.resolve).catch(e.reject);
		}
		e.async_deriveds.clear(), this.transfer_effects(e.#u, e.#d);
		let t = (e) => {
			var n = e.reactions;
			if (n !== null && !(e.f & 2 && !(e.f & 6144))) for (let e of n) {
				var r = e.f;
				if (r & 2) t(e);
				else {
					var i = e;
					r & 4194320 && !this.async_deriveds.has(i) && (this.#d.delete(i), j(i, y), this.schedule(i));
				}
			}
		};
		for (let e of this.current.keys()) t(e);
		this.oncommit(() => e.discard()), e.#S(), M = this, this.#_();
	}
	#x(e) {
		for (var t = 0; t < e.length; t += 1) Ye(e[t], this.#u, this.#d);
	}
	capture(e, n, r = !1) {
		e.v !== t && !this.previous.has(e) && this.previous.set(e, e.v), e.f & 8388608 || (this.current.set(e, [n, r]), N?.set(e, n)), this.is_fork || (e.v = n);
	}
	activate() {
		M = this;
	}
	deactivate() {
		M = null, N = null;
	}
	flush() {
		try {
			vt = !0, M = this, this.#_();
		} finally {
			xt = 0, gt = null, yt = null, bt = null, vt = !1, M = null, N = null, Mt.clear();
		}
	}
	discard() {
		for (let e of this.#i) e(this);
		this.#i.clear();
		for (let e of this.async_deriveds.values()) e.reject(at);
		this.#S(), this.#s?.resolve();
	}
	register_created_effect(e) {
		this.#l.push(e);
	}
	increment(e, t) {
		if (this.#a += 1, e) {
			let e = this.#o.get(t) ?? 0;
			this.#o.set(t, e + 1);
		}
	}
	decrement(e, t) {
		if (--this.#a, e) {
			let e = this.#o.get(t) ?? 0;
			e === 1 ? this.#o.delete(t) : this.#o.set(t, e - 1);
		}
		this.#m || (this.#m = !0, Ge(() => {
			this.#m = !1, this.linked && this.flush();
		}));
	}
	transfer_effects(e, t) {
		for (let t of e) this.#u.add(t);
		for (let e of t) this.#d.add(e);
		e.clear(), t.clear();
	}
	oncommit(e) {
		this.#r.add(e);
	}
	ondiscard(e) {
		this.#i.add(e);
	}
	settled() {
		return (this.#s ??= _()).promise;
	}
	static ensure() {
		if (M === null) {
			let t = M = new e();
			!vt && !_t && Ge(() => {
				t.#e || t.flush();
			});
		}
		return M;
	}
	apply() {
		N = null;
	}
	schedule(e) {
		if (gt = e, e.b?.is_pending && e.f & 16777228 && !(e.f & 32768)) {
			e.b.defer_effect(e);
			return;
		}
		this.#c.push(e);
	}
	#S() {
		if (this.linked) {
			var e = this.#t, t = this.#n;
			e === null || (e.#n = t), t === null ? mt = e : t.#t = e, this.linked = !1;
		}
	}
};
function wt(e) {
	var t = _t;
	_t = !0;
	try {
		var n;
		for (e && (M !== null && !M.is_fork && M.flush(), n = e());;) {
			if (Ke(), M === null) return n;
			M.flush();
		}
	} finally {
		_t = t;
	}
}
function Tt() {
	try {
		je();
	} catch (e) {
		an(e, gt);
	}
}
var Et = null;
function Dt(e) {
	var t = e.length;
	if (t !== 0) {
		for (var n = 0; n < t;) {
			var r = e[n++];
			if (!(r.f & 24576) && Bn(r) && (Et = /* @__PURE__ */ new Set(), Kn(r), r.deps === null && r.first === null && r.nodes === null && r.teardown === null && r.ac === null && xn(r), Et?.size > 0)) {
				Mt.clear();
				for (let e of Et) {
					if (e.f & 24576) continue;
					let t = [e], n = e.parent;
					for (; n !== null;) Et.has(n) && (Et.delete(n), t.push(n)), n = n.parent;
					for (let e = t.length - 1; e >= 0; e--) {
						let n = t[e];
						n.f & 24576 || Kn(n);
					}
				}
				Et.clear();
			}
		}
		Et = null;
	}
}
function Ot(e) {
	M.schedule(e);
}
function kt(e, t) {
	if (!(e.f & 32 && e.f & 1024)) {
		e.f & 2048 ? t.d.push(e) : e.f & 4096 && t.m.push(e), j(e, v);
		for (var n = e.first; n !== null;) kt(n, t), n = n.next;
	}
}
function At(e) {
	j(e, v);
	for (var t = e.first; t !== null;) At(t), t = t.next;
}
//#endregion
//#region node_modules/svelte/src/internal/client/reactivity/sources.js
var jt = /* @__PURE__ */ new Set(), Mt = /* @__PURE__ */ new Map(), Nt = !1;
function Pt(e, t) {
	return {
		f: 0,
		v: e,
		reactions: null,
		equals: Ee,
		rv: 0,
		wv: 0
	};
}
/*#__NO_SIDE_EFFECTS__*/
function P(e, t) {
	let n = Pt(e, t);
	return Nn(n), n;
}
/*#__NO_SIDE_EFFECTS__*/
function Ft(e, t = !1, n = !0) {
	let r = Pt(e);
	return t || (r.equals = Oe), r;
}
function F(e, t, n = !1) {
	return U !== null && (!W || U.f & 131072) && He() && U.f & 4325394 && (Mn === null || !Mn.has(e)) && Ie(), Rt(e, n ? Ht(t) : t, bt);
}
var It = null, Lt = 0;
function Rt(e, t, n = null) {
	if (!e.equals(t)) {
		kn ? Mt.set(e, t) : Mt.has(e) || Mt.set(e, e.v);
		var r = Ct.ensure();
		if (r.capture(e, t), e.f & 2) {
			let t = e;
			e.f & 2048 && ut(t), N === null && Je(t);
		}
		e.wv = zn(), It = null, Lt = 0, Vt(e, y, n), It = null, He() && K !== null && K.f & 1024 && !(K.f & 96) && (Y === null ? Pn([e]) : Y.push(e)), !r.is_fork && jt.size > 0 && !Nt && zt();
	}
	return t;
}
function zt() {
	Nt = !1;
	for (let e of jt) {
		e.f & 1024 && j(e, b);
		let t;
		try {
			t = Bn(e);
		} catch {
			t = !0;
		}
		t && Kn(e);
	}
	jt.clear();
}
function Bt(e) {
	F(e, e.v + 1);
}
function Vt(e, t, n) {
	var r = e.reactions;
	if (r !== null) {
		var i = He(), a = r.length;
		if (Lt += a, Lt > 1e5 && It === null && (It = /* @__PURE__ */ new Set()), It !== null) {
			if (It.has(e)) return;
			It.add(e);
		}
		for (var o = 0; o < a; o++) {
			var s = r[o], c = s.f;
			if (i || s !== K) {
				var l = (c & y) === 0;
				if (l && j(s, t), c & 131072) jt.add(s);
				else if (c & 2) {
					var u = s;
					N?.delete(u), Vt(u, b, n);
				} else if (l) {
					var d = s;
					c & 16 && Et !== null && Et.add(d), n === null ? Ot(d) : n.push(d);
				}
			}
		}
	}
}
function Ht(e) {
	if (typeof e != "object" || !e || T in e || se in e) return e;
	let n = p(e);
	if (n !== d && n !== f) return e;
	var i = /* @__PURE__ */ new Map(), a = r(e), o = /* @__PURE__ */ P(0), s = null, c = Ln, u = (e) => {
		if (Ln === c) return e();
		var t = U, n = Ln;
		G(null), Rn(c);
		var r = e();
		return G(t), Rn(n), r;
	};
	return a && i.set("length", /* @__PURE__ */ P(e.length, s)), new Proxy(e, {
		defineProperty(e, t, n) {
			(!("value" in n) || n.configurable === !1 || n.enumerable === !1 || n.writable === !1) && Pe();
			var r = i.get(t);
			return r === void 0 ? u(() => {
				var e = /* @__PURE__ */ P(n.value, s);
				return i.set(t, e), e;
			}) : F(r, n.value, !0), !0;
		},
		deleteProperty(e, n) {
			var r = i.get(n);
			if (r === void 0) {
				if (n in e) {
					let e = u(() => /* @__PURE__ */ P(t, s));
					i.set(n, e), Bt(o);
				}
			} else F(r, t), Bt(o);
			return !0;
		},
		get(n, r, a) {
			if (r === T) return e;
			var o = i.get(r), c = r in n;
			if (o === void 0 && (!c || l(n, r)?.writable) && (o = u(() => /* @__PURE__ */ P(Ht(c ? n[r] : t), s)), i.set(r, o)), o !== void 0) {
				var d = X(o);
				return d === t ? void 0 : d;
			}
			return Reflect.get(n, r, a);
		},
		getOwnPropertyDescriptor(e, n) {
			this.has?.(e, n);
			var r = Reflect.getOwnPropertyDescriptor(e, n), a = i.get(n);
			if (a !== void 0) {
				var o = X(a);
				if (o === t) return;
				if (r && "value" in r) r.value = o;
				else return {
					enumerable: !0,
					configurable: !0,
					value: o,
					writable: !0
				};
			}
			return r;
		},
		has(e, n) {
			if (n === T) return !0;
			var r = i.get(n), a = r !== void 0 && r.v !== t || Reflect.has(e, n);
			return (r !== void 0 || K !== null && (!a || l(e, n)?.writable)) && (r === void 0 && (r = u(() => /* @__PURE__ */ P(a ? Ht(e[n]) : t, s)), i.set(n, r)), X(r) === t) ? !1 : a;
		},
		set(e, n, r, c) {
			var d = i.get(n), f = n in e;
			if (a && n === "length") for (var p = r; p < d.v; p += 1) {
				var m = i.get(p + "");
				m === void 0 ? p in e && (m = u(() => /* @__PURE__ */ P(t, s)), i.set(p + "", m)) : F(m, t);
			}
			if (d === void 0) (!f || l(e, n)?.writable) && (d = u(() => /* @__PURE__ */ P(void 0, s)), F(d, Ht(r)), i.set(n, d));
			else {
				f = d.v !== t;
				var h = u(() => Ht(r));
				F(d, h);
			}
			var g = Reflect.getOwnPropertyDescriptor(e, n);
			if (g?.set && g.set.call(c, r), !f) {
				if (a && typeof n == "string") {
					var _ = i.get("length"), v = Number(n);
					Number.isInteger(v) && v >= _.v && F(_, v + 1);
				}
				Bt(o);
			}
			return !0;
		},
		ownKeys(e) {
			X(o);
			var n = Reflect.ownKeys(e).filter((e) => {
				var n = i.get(e);
				return n === void 0 || n.v !== t;
			});
			for (var [r, a] of i) a.v !== t && !(r in e) && n.push(r);
			return n;
		},
		setPrototypeOf() {
			Fe();
		}
	});
}
function Ut(e) {
	try {
		if (typeof e == "object" && e && T in e) return e[T];
	} catch {}
	return e;
}
function Wt(e, t) {
	return Object.is(Ut(e), Ut(t));
}
var Gt, Kt, qt, Jt;
function Yt() {
	if (Gt === void 0) {
		Gt = window, Kt = /Firefox/.test(navigator.userAgent);
		var e = Element.prototype, t = Node.prototype, n = Text.prototype;
		qt = l(t, "firstChild").get, Jt = l(t, "nextSibling").get, m(e) && (e[de] = void 0, e[ue] = null, e[fe] = void 0, e.__e = void 0), m(n) && (n[pe] = void 0);
	}
}
function I(e = "") {
	return document.createTextNode(e);
}
/*@__NO_SIDE_EFFECTS__*/
function Xt(e) {
	return qt.call(e);
}
/*@__NO_SIDE_EFFECTS__*/
function Zt(e) {
	return Jt.call(e);
}
function L(e, t) {
	if (!E) return /* @__PURE__ */ Xt(e);
	var n = /* @__PURE__ */ Xt(D);
	if (n === null) n = D.appendChild(I());
	else if (t && n.nodeType !== 3) {
		var r = I();
		return n?.before(r), O(r), r;
	}
	return t && nn(n), O(n), n;
}
function Qt(e, t = !1) {
	if (!E) {
		var n = /* @__PURE__ */ Xt(e);
		return n instanceof Comment && n.data === "" ? /* @__PURE__ */ Zt(n) : n;
	}
	if (t) {
		if (D?.nodeType !== 3) {
			var r = I();
			return D?.before(r), O(r), r;
		}
		nn(D);
	}
	return D;
}
function R(e, t = !1) {
	if (!E) return /* @__PURE__ */ Xt(e);
	var n = L(e, t);
	return k(e), n;
}
function z(e, t = 1, n = !1) {
	let r = E ? D : e;
	for (var i; t--;) i = r, r = /* @__PURE__ */ Zt(r);
	if (!E) return r;
	if (n) {
		if (r?.nodeType !== 3) {
			var a = I();
			return r === null ? i?.after(a) : r.before(a), O(a), a;
		}
		nn(r);
	}
	return O(r), r;
}
function $t(e) {
	e.textContent = "";
}
function en() {
	return !1;
}
function tn(e, t, n) {
	return t == null || t === "http://www.w3.org/1999/xhtml" ? n ? document.createElement(e, { is: n }) : document.createElement(e) : n ? document.createElementNS(t, e, { is: n }) : document.createElementNS(t, e);
}
function nn(e) {
	if (e.nodeValue.length < 65536) return;
	let t = e.nextSibling;
	for (; t !== null && t.nodeType === 3;) t.remove(), e.nodeValue += t.nodeValue, t = e.nextSibling;
}
function rn(e) {
	var t = K;
	if (t === null) return U.f |= oe, e;
	if (!(t.f & 32768) && !(t.f & 4)) throw e;
	an(e, t);
}
function an(e, t) {
	if (!(t !== null && t.f & 16384)) {
		for (; t !== null;) {
			if (t.f & 128 && !(t.f & 33570816)) {
				if (!(t.f & 32768)) throw e;
				try {
					t.b.error(e);
					return;
				} catch (t) {
					e = t;
				}
			}
			t = t.parent;
		}
		throw e;
	}
}
//#endregion
//#region node_modules/svelte/src/internal/client/reactivity/effects.js
function on(e, t) {
	var n = t.last;
	n === null ? t.last = t.first = e : (n.next = e, e.prev = n, t.last = e);
}
function sn(e, t) {
	var n = K;
	n !== null && n.f & 8192 && (e |= x);
	var r = {
		ctx: A,
		deps: null,
		nodes: null,
		f: e | y | 512,
		first: null,
		fn: t,
		last: null,
		next: null,
		parent: n,
		b: n && n.b,
		prev: null,
		teardown: null,
		wv: 0,
		ac: null
	};
	M?.register_created_effect(r);
	var i = r;
	if (e & 4) yt === null ? Ct.ensure().schedule(r) : yt.push(r);
	else if (t !== null) {
		try {
			Kn(r);
		} catch (e) {
			throw H(r), e;
		}
		i.deps === null && i.teardown === null && i.nodes === null && i.first === i.last && !(i.f & 524288) && (i = i.first, e & 16 && e & 65536 && i !== null && (i.f |= te));
	}
	if (i !== null && (i.parent = n, n !== null && on(i, n), U !== null && U.f & 2 && !(e & 64))) {
		var a = U;
		(a.effects ??= []).push(i);
	}
	return r;
}
function cn() {
	return U !== null && !W;
}
function ln(e) {
	let t = sn(8, null);
	return j(t, v), t.teardown = e, t;
}
function un(e) {
	return sn(4 | ne, e);
}
function dn(e) {
	Ct.ensure();
	let t = sn(64 | w, e);
	return () => {
		H(t);
	};
}
function fn(e) {
	Ct.ensure();
	let t = sn(64 | w, e);
	return (e = {}) => new Promise((n) => {
		e.outro ? Sn(t, () => {
			H(t), n(void 0);
		}) : (H(t), n(void 0));
	});
}
function pn(e) {
	return sn(4, e);
}
function mn(e) {
	return sn(ae | w, e);
}
function hn(e, t = 0) {
	return sn(8 | t, e);
}
function B(e, t = [], n = [], r = []) {
	et(r, t, n, (t) => {
		sn(8, () => {
			e(...t.map(X));
		});
	});
}
function gn(e, t = 0) {
	return sn(16 | t, e);
}
function V(e) {
	return sn(32 | w, e);
}
function _n(e) {
	var t = e.teardown;
	if (t !== null) {
		let n = kn, r = U;
		An(!0), G(null);
		try {
			t.call(null);
		} catch (t) {
			an(t, e.parent);
		} finally {
			An(n), G(r);
		}
	}
}
function vn(e, t = !1) {
	var n = e.first;
	for (e.first = e.last = null; n !== null;) {
		let e = n.ac;
		e !== null && Qe(() => {
			e.abort(he);
		});
		var r = n.next;
		n.f & 64 ? n.parent = null : H(n, t), n = r;
	}
}
function yn(e) {
	for (var t = e.first; t !== null;) {
		var n = t.next;
		t.f & 32 || H(t), t = n;
	}
}
function H(e, t = !0) {
	var n = !1;
	(t || e.f & 262144) && e.nodes !== null && e.nodes.end !== null && (bn(e.nodes.start, e.nodes.end), n = !0), e.f |= C, vn(e, t && !n), Gn(e, 0);
	var r = e.nodes && e.nodes.t;
	if (r !== null) for (let e of r) e.stop();
	_n(e), e.f ^= C, e.f |= S;
	var i = e.parent;
	i !== null && i.first !== null && xn(e), e.next = e.prev = e.teardown = e.ctx = e.deps = e.fn = e.nodes = e.ac = e.b = null;
}
function bn(e, t) {
	for (; e !== null;) {
		var n = e === t ? null : /* @__PURE__ */ Zt(e);
		e.remove(), e = n;
	}
}
function xn(e) {
	var t = e.parent, n = e.prev, r = e.next;
	n !== null && (n.next = r), r !== null && (r.prev = n), t !== null && (t.first === e && (t.first = r), t.last === e && (t.last = n));
}
function Sn(e, t, n = !0) {
	var r = [];
	e.f |= 256, Cn(e, r, !0);
	var i = () => {
		n && H(e), t && t();
	}, a = r.length;
	if (a > 0) {
		var o = () => --a || i();
		for (var s of r) s.out(o);
	} else i();
}
function Cn(e, t, n) {
	if (!(e.f & 8192)) {
		e.f ^= x;
		var r = e.nodes && e.nodes.t;
		if (r !== null) for (let e of r) (e.is_global || n) && t.push(e);
		for (var i = e.first; i !== null;) {
			var a = i.next;
			if (!(i.f & 64)) {
				var o = !!(i.f & 65536) || !!(i.f & 32) && !!(e.f & 16);
				Cn(i, t, o ? n : !1);
			}
			i = a;
		}
	}
}
function wn(e) {
	e.f &= -257, Tn(e, !0);
}
function Tn(e, t) {
	if (!(e.f & 256) && e.f & 8192) {
		e.f ^= x, e.f & 1024 || (j(e, y), Ct.ensure().schedule(e));
		for (var n = e.first; n !== null;) {
			var r = n.next, i = !!(n.f & 65536) || !!(n.f & 32);
			Tn(n, i ? t : !1), n = r;
		}
		var a = e.nodes && e.nodes.t;
		if (a !== null) for (let e of a) (e.is_global || t) && e.in();
	}
}
function En(e, t) {
	if (e.nodes) for (var n = e.nodes.start, r = e.nodes.end; n !== null;) {
		var i = n === r ? null : /* @__PURE__ */ Zt(n);
		t.append(n), n = i;
	}
}
//#endregion
//#region node_modules/svelte/src/internal/client/legacy.js
var Dn = null, On = !1, kn = !1;
function An(e) {
	kn = e;
}
var U = null, W = !1;
function G(e) {
	U = e;
}
var K = null;
function jn(e) {
	K = e;
}
var Mn = null;
function Nn(e) {
	U !== null && (U.f & 2097152 || U.f & 2) && (Mn ??= /* @__PURE__ */ new Set()).add(e);
}
var q = null, J = 0, Y = null;
function Pn(e) {
	Y = e;
}
var Fn = 1, In = 0, Ln = In;
function Rn(e) {
	Ln = e;
}
function zn() {
	return ++Fn;
}
function Bn(e) {
	var t = e.f;
	if (t & 2048) return !0;
	if (t & 4096) {
		for (var n = e.deps, r = n.length, i = 0; i < r; i++) {
			var a = n[i];
			if (Bn(a) && dt(a), a.wv > e.wv) return !0;
		}
		t & 512 && N === null && j(e, v);
	}
	return !1;
}
function Vn(e, t, n = !0) {
	var r = e.reactions;
	if (r !== null && !(Mn !== null && Mn.has(e))) for (var i = 0; i < r.length; i++) {
		var a = r[i];
		a.f & 2 ? Vn(a, t, !1) : t === a && (n ? j(a, y) : a.f & 1024 && j(a, b), Ot(a));
	}
}
function Hn(e) {
	var t = q, n = J, r = Y, i = U, a = Mn, o = A, s = W, c = Ln, l = e.f;
	q = null, J = 0, Y = null, U = l & 96 ? null : e, Mn = null, Re(e.ctx), W = !1, Ln = ++In, e.ac !== null && (Qe(() => {
		e.ac.abort(he);
	}), e.ac = null);
	try {
		e.f |= ie;
		var u = e.fn, d = u();
		e.f |= ee;
		var f = Un(e);
		if (He() && Y !== null && !W && f !== null && !(e.f & 6146)) for (var p = 0; p < Y.length; p++) Vn(Y[p], e);
		if (i !== null && i !== e) {
			if (In++, i.deps !== null) for (let e = 0; e < n; e += 1) i.deps[e].rv = In;
			if (t !== null) for (let e of t) e.rv = In;
			Y !== null && (r === null ? r = Y : r.push(...Y));
		}
		return e.f & 8388608 && (e.f ^= oe), d;
	} catch (t) {
		return Un(e), rn(t);
	} finally {
		e.f ^= ie, q = t, J = n, Y = r, U = i, Mn = a, Re(o), W = s, Ln = c;
	}
}
function Un(e) {
	var t = e.deps, n = M?.is_fork;
	if (q !== null) {
		var r;
		if (n || Gn(e, J), t !== null && J > 0) for (t.length = J + q.length, r = 0; r < q.length; r++) t[J + r] = q[r];
		else e.deps = t = q;
		if (cn() && e.f & 512) for (r = J; r < t.length; r++) (t[r].reactions ??= []).push(e);
	} else !n && t !== null && J < t.length && (Gn(e, J), t.length = J);
	return t;
}
function Wn(e, n) {
	let r = n.reactions;
	if (r !== null) {
		var o = i.call(r, e);
		if (o !== -1) {
			var s = r.length - 1;
			s === 0 ? r = n.reactions = null : (r[o] = r[s], r.pop());
		}
	}
	if (r === null && n.f & 2 && (q === null || !a.call(q, n))) {
		var c = n;
		c.f & 512 && (c.f ^= 512), c.v !== t && Je(c), c.ac !== null && Qe(() => {
			c.ac.abort(he), c.ac = null, j(c, y);
		}), ft(c), Gn(c, 0);
	}
}
function Gn(e, t) {
	var n = e.deps;
	if (n !== null) for (var r = t; r < n.length; r++) Wn(e, n[r]);
}
function Kn(e) {
	var t = e.f;
	if (!(t & 16384)) {
		j(e, v);
		var n = K, r = On;
		K = e, On = !(t & 96);
		try {
			t & 16777232 ? yn(e) : vn(e), _n(e);
			var i = Hn(e);
			e.teardown = typeof i == "function" ? i : null, e.wv = Fn;
		} finally {
			On = r, K = n;
		}
	}
}
async function qn() {
	await Promise.resolve(), wt();
}
function X(e) {
	var t = !!(e.f & 2);
	if (Dn?.add(e), U !== null && !W && !(K !== null && K.f & 16384) && (Mn === null || !Mn.has(e))) {
		var n = U.deps;
		if (U.f & 2097152) e.rv < In && (e.rv = In, q === null && n !== null && n[J] === e ? J++ : q === null ? q = [e] : q.push(e));
		else {
			U.deps ??= [], a.call(U.deps, e) || U.deps.push(e);
			var r = e.reactions;
			r === null ? e.reactions = [U] : a.call(r, U) || r.push(U);
		}
	}
	if (kn && Mt.has(e)) return Mt.get(e);
	if (t) {
		var i = e;
		if (kn) {
			var o = i.v;
			return (!(i.f & 1024) && i.reactions !== null || Yn(i)) && (o = ut(i)), Mt.set(i, o), o;
		}
		var s = !(i.f & 512) && !W && U !== null && (On || !!(U.f & 512)), c = (i.f & ee) === 0;
		Bn(i) && (s && (i.f |= 512), dt(i)), s && !c && (pt(i), Jn(i));
	}
	if (N?.has(e)) return N.get(e);
	if (e.f & 8388608) throw e.v;
	return e.v;
}
function Jn(e) {
	if (e.f |= 512, e.deps !== null) for (let t of e.deps) (t.reactions ??= []).push(e), t.f & 2 && !(t.f & 512) && (pt(t), Jn(t));
}
function Yn(e) {
	if (e.v === t) return !0;
	if (e.deps === null) return !1;
	for (let t of e.deps) if (Mt.has(t) || t.f & 2 && Yn(t)) return !0;
	return !1;
}
function Xn(e) {
	var t = W;
	try {
		return W = !0, e();
	} finally {
		W = t;
	}
}
//#endregion
//#region node_modules/svelte/src/internal/client/dom/elements/events.js
var Zn = Symbol("events"), Qn = /* @__PURE__ */ new Set(), $n = /* @__PURE__ */ new Set();
function er(e, t, n) {
	(t[Zn] ??= {})[e] = n;
}
function tr(e) {
	for (var t = 0; t < e.length; t++) Qn.add(e[t]);
	for (var n of $n) n(e);
}
var nr = null, rr = !1;
function ir(e) {
	var t = this, n = t.ownerDocument, r = e.type, i = e.composedPath?.() || [], a = i[0] || e.target;
	nr = e, rr || (rr = !0, setTimeout(() => {
		rr = !1, nr = null;
	}));
	var o = 0, s = nr === e && e[Zn];
	if (s) {
		var l = i.indexOf(s);
		if (l !== -1 && (t === document || t === window)) {
			e[Zn] = t;
			return;
		}
		var u = i.indexOf(t);
		if (u === -1) return;
		l <= u && (o = l);
	}
	if (a = i[o] || e.target, a !== t) {
		c(e, "currentTarget", {
			configurable: !0,
			get() {
				return a || n;
			}
		});
		var d = U, f = K;
		G(null), jn(null);
		try {
			for (var p, m = []; a !== null && a !== t;) {
				try {
					var h = a[Zn]?.[r];
					h != null && (!a.disabled || e.target === a) && h.call(a, e);
				} catch (e) {
					p ? m.push(e) : p = e;
				}
				if (e.cancelBubble) break;
				o++, a = o < i.length ? i[o] : null;
			}
			if (p) {
				for (let e of m) queueMicrotask(() => {
					throw e;
				});
				throw p;
			}
		} finally {
			e[Zn] = t, delete e.currentTarget, G(d), jn(f);
		}
	}
}
//#endregion
//#region node_modules/svelte/src/internal/client/dom/reconciler.js
var ar = globalThis?.window?.trustedTypes && /* @__PURE__ */ globalThis.window.trustedTypes.createPolicy("svelte-trusted-html", { createHTML: (e) => e });
function or(e) {
	return ar?.createHTML(e) ?? e;
}
function sr(e) {
	var t = tn("template");
	return t.innerHTML = or(e.replaceAll("<!>", "<!---->")), t.content;
}
//#endregion
//#region node_modules/svelte/src/internal/client/dom/template.js
function cr(e, t) {
	var n = K;
	n.nodes === null && (n.nodes = {
		start: e,
		end: t,
		a: null,
		t: null
	});
}
/*#__NO_SIDE_EFFECTS__*/
function lr(e, t) {
	var n = !!(t & 1), r = !!(t & 2), i, a = !e.startsWith("<!>");
	return () => {
		if (E) return cr(D, null), D;
		i === void 0 && (i = sr(a ? e : "<!>" + e), n || (i = /* @__PURE__ */ Xt(i)));
		var t = r || Kt ? document.importNode(i, !0) : i.cloneNode(!0);
		if (n) {
			var o = /* @__PURE__ */ Xt(t), s = t.lastChild;
			cr(o, s);
		} else cr(t, t);
		return t;
	};
}
function Z(e, t) {
	if (E) {
		var n = K;
		(!(n.f & 32768) || n.nodes.end === null) && (n.nodes.end = D), Se();
		return;
	}
	e !== null && e.before(t);
}
[.../* @__PURE__ */ "allowfullscreen.async.autofocus.autoplay.checked.controls.default.disabled.formnovalidate.indeterminate.inert.ismap.loop.multiple.muted.nomodule.novalidate.open.playsinline.readonly.required.reversed.seamless.selected.webkitdirectory.defer.disablepictureinpicture.disableremoteplayback".split(".")];
var ur = ["touchstart", "touchmove"];
function dr(e) {
	return ur.includes(e);
}
//#endregion
//#region node_modules/svelte/src/reactivity/create-subscriber.js
function fr(e) {
	let t = 0, n = Pt(0), r;
	return () => {
		cn() && (X(n), hn(() => (t === 0 && (r = Xn(() => e(() => Bt(n)))), t += 1, () => {
			Ge(() => {
				--t, t === 0 && (r?.(), r = void 0, Bt(n));
			});
		})));
	};
}
//#endregion
//#region node_modules/svelte/src/internal/client/dom/blocks/boundary.js
var pr = te | w;
function mr(e, t, n, r) {
	new hr(e, t, n, r);
}
var hr = class {
	parent;
	is_pending = !1;
	transform_error;
	#e;
	#t = E ? D : null;
	#n;
	#r;
	#i;
	#a = null;
	#o = null;
	#s = null;
	#c = null;
	#l = 0;
	#u = 0;
	#d = !1;
	#f = /* @__PURE__ */ new Set();
	#p = /* @__PURE__ */ new Set();
	#m = null;
	#h = fr(() => (this.#m = Pt(this.#l), () => {
		this.#m = null;
	}));
	constructor(e, t, n, r) {
		this.#e = e, this.#n = t, this.#r = (e) => {
			var t = K;
			t.b = this, t.f |= 128, n(e);
		}, this.parent = K.b, this.transform_error = r ?? this.parent?.transform_error ?? ((e) => e), this.#i = gn(() => {
			if (E) {
				let e = this.#t;
				Se();
				let t = e.data === "[!";
				if (e.data.startsWith("[?")) {
					let t = JSON.parse(e.data.slice(2));
					this.#_(t);
				} else t ? this.#y() : this.#g();
			} else this.#b();
		}, pr), E && (this.#e = D);
	}
	#g() {
		try {
			this.#a = V(() => this.#r(this.#e));
		} catch (e) {
			this.error(e);
		}
	}
	#_(e) {
		let t = this.#n.failed, { reset: n, invoke_onerror: r } = this.#v(e);
		Ge(r), t && (this.#s = V(() => {
			t(this.#e, () => e, () => n);
		}));
	}
	#v(e) {
		var t = !1, n = !1;
		let r = () => {
			if (t) {
				be();
				return;
			}
			t = !0, n && Le(), this.#s !== null && Sn(this.#s, () => {
				this.#s = null;
			}), this.#S(() => {
				this.#b();
			});
		};
		return {
			reset: r,
			invoke_onerror: () => {
				try {
					n = !0, this.#n.onerror?.(e, r), n = !1;
				} catch (e) {
					an(e, this.#i && this.#i.parent);
				}
			}
		};
	}
	#y() {
		let e = this.#n.pending;
		e && (this.is_pending = !0, this.#o = V(() => e(this.#e)), Ge(() => {
			var e = this.#c = document.createDocumentFragment(), t = I(), n = !1;
			if (e.append(t), this.#a = this.#S(() => {
				try {
					return V(() => this.#r(t));
				} catch (e) {
					try {
						this.error(e), n = !0;
					} catch (e) {
						an(e, this.#i.parent);
					}
					return null;
				}
			}), this.#a === null) {
				this.#c = null, n && this.#x(M);
				return;
			}
			this.#u === 0 && (this.#e.before(e), this.#c = null, Sn(this.#o, () => {
				this.#o = null;
			}), this.#x(M));
		}));
	}
	#b() {
		try {
			if (this.is_pending = this.has_pending_snippet(), this.#u = 0, this.#l = 0, this.#a = V(() => {
				this.#r(this.#e);
			}), this.#u > 0) {
				var e = this.#c = document.createDocumentFragment();
				En(this.#a, e);
				let t = this.#n.pending;
				this.#o = V(() => t(this.#e));
			} else this.#x(M);
		} catch (e) {
			this.error(e);
		}
	}
	#x(e) {
		this.is_pending = !1, e.transfer_effects(this.#f, this.#p);
	}
	defer_effect(e) {
		Ye(e, this.#f, this.#p);
	}
	is_rendered() {
		return !this.is_pending && (!this.parent || this.parent.is_rendered());
	}
	has_pending_snippet() {
		return !!this.#n.pending;
	}
	#S(e) {
		var t = K, n = U, r = A;
		jn(this.#i), G(this.#i), Re(this.#i.ctx);
		try {
			return Ct.ensure(), e();
		} finally {
			jn(t), G(n), Re(r);
		}
	}
	#C(e, t) {
		if (!this.has_pending_snippet()) {
			this.parent && this.parent.#C(e, t);
			return;
		}
		this.#u += e, this.#u === 0 && (this.#x(t), this.#o && Sn(this.#o, () => {
			this.#o = null;
		}), this.#c &&= (this.#e.before(this.#c), null));
	}
	update_pending_count(e, t) {
		this.#C(e, t), this.#l += e, !(!this.#m || this.#d) && (this.#d = !0, Ge(() => {
			this.#d = !1, this.#m && Rt(this.#m, this.#l);
		}));
	}
	get_effect_pending() {
		return this.#h(), X(this.#m);
	}
	error(e) {
		if (!this.#n.onerror && !this.#n.failed) throw e;
		M?.is_fork ? (this.#a && M.skip_effect(this.#a), this.#o && M.skip_effect(this.#o), this.#s && M.skip_effect(this.#s), M.oncommit(() => {
			this.#w(e);
		})) : this.#w(e);
	}
	#w(e) {
		this.#a &&= (H(this.#a), null), this.#o &&= (H(this.#o), null), this.#s &&= (H(this.#s), null), E && (O(this.#t), Ce(), O(we()));
		let t = this.#n.failed, n = (e) => {
			let { reset: n, invoke_onerror: r } = this.#v(e);
			r(), t && (this.#s = this.#S(() => {
				try {
					return V(() => {
						var r = K;
						r.b = this, r.f |= 128, t(this.#e, () => e, () => n);
					});
				} catch (e) {
					return an(e, this.#i.parent), null;
				}
			}));
		};
		Ge(() => {
			var t;
			try {
				t = this.transform_error(e);
			} catch (e) {
				an(e, this.#i && this.#i.parent);
				return;
			}
			typeof t == "object" && t && typeof t.then == "function" ? t.then(n, (e) => an(e, this.#i && this.#i.parent)) : n(t);
		});
	}
};
function Q(e, t) {
	var n = t == null ? "" : typeof t == "object" ? `${t}` : t;
	n !== (e[pe] ??= e.nodeValue) && (e[pe] = n, e.nodeValue = `${n}`);
}
function gr(e, t) {
	return yr(e, t);
}
function _r(t, n) {
	Yt(), n.intro = n.intro ?? !1;
	let r = n.target, i = E, a = D;
	try {
		for (var o = /* @__PURE__ */ Xt(r); o && (o.nodeType !== 8 || o.data !== "[");) o = /* @__PURE__ */ Zt(o);
		if (!o) throw e;
		xe(!0), O(o);
		let i = yr(t, {
			...n,
			anchor: o
		});
		return xe(!1), i;
	} catch (i) {
		if (i instanceof Error && i.message.split("\n").some((e) => e.startsWith("https://svelte.dev/e/"))) throw i;
		return i !== e && console.warn("Failed to hydrate: ", i), n.recover === !1 && Me(), Yt(), $t(r), xe(!1), gr(t, n);
	} finally {
		xe(i), O(a);
	}
}
var vr = /* @__PURE__ */ new Map();
function yr(t, { target: n, anchor: r, props: i = {}, events: a, context: s, intro: c = !0, transformError: l }) {
	Yt();
	var u = void 0, d = fn(() => {
		var c = r ?? n.appendChild(I());
		mr(c, { pending: () => {} }, (n) => {
			ze({});
			var r = A;
			if (s && (r.c = s), a && (i.$$events = a), E && cr(n, null), u = t(n, i) || Ve(), E && (K.nodes.end = D, D === null || D.nodeType !== 8 || D.data !== "]")) throw ve(), e;
			Be();
		}, l);
		var d = /* @__PURE__ */ new Set(), f = (e) => {
			for (var t = 0; t < e.length; t++) {
				var r = e[t];
				if (!d.has(r)) {
					d.add(r);
					var i = dr(r);
					for (let e of [n, document]) {
						var a = vr.get(e);
						a === void 0 && (a = /* @__PURE__ */ new Map(), vr.set(e, a));
						var o = a.get(r);
						o === void 0 ? (e.addEventListener(r, ir, { passive: i }), a.set(r, 1)) : a.set(r, o + 1);
					}
				}
			}
		};
		return f(o(Qn)), $n.add(f), () => {
			for (var e of d) for (let r of [n, document]) {
				var t = vr.get(r), i = t.get(e);
				--i == 0 ? (r.removeEventListener(e, ir), t.delete(e), t.size === 0 && vr.delete(r)) : t.set(e, i);
			}
			$n.delete(f), c !== r && c.parentNode?.removeChild(c);
		};
	});
	return br.set(u, d), u;
}
var br = /* @__PURE__ */ new WeakMap();
function xr(e, t) {
	let n = br.get(e);
	return n ? (br.delete(e), n(t)) : Promise.resolve();
}
//#endregion
//#region node_modules/svelte/src/internal/client/dom/blocks/branches.js
var Sr = class {
	anchor;
	#e = /* @__PURE__ */ new Map();
	#t = /* @__PURE__ */ new Map();
	#n = /* @__PURE__ */ new Map();
	#r = /* @__PURE__ */ new Set();
	#i = !0;
	constructor(e, t = !0) {
		this.anchor = e, this.#i = t;
	}
	#a = (e) => {
		if (this.#e.has(e)) {
			var t = this.#e.get(e), n = this.#t.get(t);
			if (n) wn(n), this.#r.delete(t);
			else {
				var r = this.#n.get(t);
				r && (wn(r.effect), this.#t.set(t, r.effect), this.#n.delete(t), r.fragment.lastChild.remove(), this.anchor.before(r.fragment), n = r.effect);
			}
			for (let [t, n] of this.#e) {
				if (this.#e.delete(t), t === e) break;
				let r = this.#n.get(n);
				r && (H(r.effect), this.#n.delete(n));
			}
			for (let [e, r] of this.#t) {
				if (e === t || this.#r.has(e)) continue;
				let i = () => {
					if (Array.from(this.#e.values()).includes(e)) {
						var t = document.createDocumentFragment();
						En(r, t), t.append(I()), this.#n.set(e, {
							effect: r,
							fragment: t
						});
					} else H(r);
					this.#r.delete(e), this.#t.delete(e);
				};
				this.#i || !n ? (this.#r.add(e), Sn(r, i, !1)) : i();
			}
		}
	};
	#o = (e) => {
		this.#e.delete(e);
		let t = Array.from(this.#e.values());
		for (let [e, n] of this.#n) t.includes(e) || (H(n.effect), this.#n.delete(e));
	};
	ensure(e, t) {
		var n = M, r = en();
		if (t && !this.#t.has(e) && !this.#n.has(e)) {
			if (r) {
				var i = document.createDocumentFragment(), a = I();
				i.append(a), this.#n.set(e, {
					effect: V(() => t(a)),
					fragment: i
				});
			} else this.#t.set(e, V(() => t(this.anchor)));
		}
		if (this.#e.set(n, e), r) {
			for (let [t, r] of this.#t) t === e ? n.unskip_effect(r) : n.skip_effect(r);
			for (let [t, r] of this.#n) t === e ? n.unskip_effect(r.effect) : n.skip_effect(r.effect);
			n.oncommit(this.#a), n.ondiscard(this.#o);
		} else E && (this.anchor = D), this.#a(n);
	}
};
//#endregion
//#region node_modules/svelte/src/internal/client/dom/blocks/if.js
function Cr(e, t, n = !1) {
	var r;
	E && (r = D, Se());
	var i = new Sr(e), a = n ? te : 0;
	function o(e, t) {
		if (E) {
			var n = Te(r);
			if (e !== parseInt(n.substring(1))) {
				var a = we();
				O(a), i.anchor = a, xe(!1), i.ensure(e, t), xe(!0);
				return;
			}
		}
		i.ensure(e, t);
	}
	gn(() => {
		var e = !1;
		t((t, n = 0) => {
			e = !0, o(n, t);
		}), e || o(-1, null);
	}, a);
}
//#endregion
//#region node_modules/svelte/src/internal/client/dom/blocks/each.js
function wr(e, t, n) {
	for (var r = [], i = t.length, a, s = t.length, c = 0; c < i; c++) {
		let n = t[c];
		Sn(n, () => {
			if (a) {
				if (a.pending.delete(n), a.done.add(n), a.pending.size === 0) {
					var t = e.outrogroups;
					Tr(e, o(a.done)), t.delete(a), t.size === 0 && (e.outrogroups = null);
				}
			} else --s;
		}, !1);
	}
	if (s === 0) {
		var l = r.length === 0 && n !== null && e.pending.size === 0;
		if (l) {
			var u = n, d = u.parentNode;
			$t(d), d.append(u), e.items.clear();
		}
		Tr(e, t, !l);
	} else a = {
		pending: new Set(t),
		done: /* @__PURE__ */ new Set()
	}, (e.outrogroups ??= /* @__PURE__ */ new Set()).add(a);
}
function Tr(e, t, n = !0) {
	var r;
	if (e.pending.size > 0) {
		r = /* @__PURE__ */ new Set();
		for (let t of e.pending.values()) for (let n of t) r.add(e.items.get(n).e);
	}
	for (var i = 0; i < t.length; i++) {
		var a = t[i];
		r?.has(a) ? (a.f |= re, En(a, document.createDocumentFragment())) : H(t[i], n);
	}
}
var Er;
function Dr(e, t, n, i, a, s = null) {
	var c = e, l = /* @__PURE__ */ new Map();
	if (t & 4) {
		var u = e;
		c = E ? O(/* @__PURE__ */ Xt(u)) : u.appendChild(I());
	}
	E && Se();
	var d = null, f = /* @__PURE__ */ ct(() => {
		var e = n();
		return r(e) ? e : e == null ? [] : o(e);
	}), p, m = /* @__PURE__ */ new Map(), h = !0;
	function g(e) {
		v.effect.f & 16384 || (v.pending.delete(e), v.fallback = d, kr(v, p, c, t, i), d !== null && (p.length === 0 ? d.f & 33554432 ? (d.f ^= re, jr(d, null, c)) : wn(d) : Sn(d, () => {
			d = null;
		})));
	}
	function _(e) {
		v.pending.delete(e);
	}
	var v = {
		effect: gn(() => {
			p = X(f);
			var e = p.length;
			let r = !1;
			E && Te(c) === "[!" != (e === 0) && (c = we(), O(c), xe(!1), r = !0);
			for (var o = /* @__PURE__ */ new Set(), u = M, v = en(), y = 0; y < e; y += 1) {
				E && D.nodeType === 8 && D.data === "]" && (c = D, r = !0, xe(!1));
				var b = p[y], x = i(b, y), S = h ? null : l.get(x);
				S ? (S.v && Rt(S.v, b), S.i && Rt(S.i, y), v && u.unskip_effect(S.e)) : (S = Ar(l, h ? c : Er ??= I(), b, x, y, a, t, n), h || (S.e.f |= re), l.set(x, S)), o.add(x);
			}
			if (e === 0 && s && !d && (h ? d = V(() => s(c)) : (d = V(() => s(Er ??= I())), d.f |= re)), e > o.size && Ae("", "", ""), E && e > 0 && O(we()), !h) {
				if (m.set(u, o), v) {
					for (let [e, t] of l) o.has(e) || u.skip_effect(t.e);
					u.oncommit(g), u.ondiscard(_);
				} else g(u);
			}
			r && xe(!0), X(f);
		}),
		flags: t,
		items: l,
		pending: m,
		outrogroups: null,
		fallback: d
	};
	h = !1, E && (c = D);
}
function Or(e) {
	for (; e !== null && !(e.f & 32);) e = e.next;
	return e;
}
function kr(e, t, n, r, i) {
	var a = !!(r & 8), s = t.length, c = e.items, l = Or(e.effect.first), u, d = null, f, p = [], m = [], h, g, _, v;
	if (a) for (v = 0; v < s; v += 1) h = t[v], g = i(h, v), _ = c.get(g).e, _.f & 33554432 || (_.nodes?.a?.measure(), (f ??= /* @__PURE__ */ new Set()).add(_));
	for (v = 0; v < s; v += 1) {
		if (h = t[v], g = i(h, v), _ = c.get(g).e, e.outrogroups !== null) for (let t of e.outrogroups) t.pending.delete(_), t.done.delete(_);
		if (_.f & 8192 && (wn(_), a && (_.nodes?.a?.unfix(), (f ??= /* @__PURE__ */ new Set()).delete(_))), _.f & 33554432) {
			if (_.f ^= re, _ === l) jr(_, null, n);
			else {
				var y = d ? d.next : l;
				_ === e.effect.last && (e.effect.last = _.prev), _.prev && (_.prev.next = _.next), _.next && (_.next.prev = _.prev), Mr(e, d, _), Mr(e, _, y), jr(_, y, n), d = _, p = [], m = [], l = Or(d.next);
				continue;
			}
		}
		if (_ !== l) {
			if (u !== void 0 && u.has(_)) {
				if (p.length < m.length) {
					var b = m[0], x;
					d = b.prev;
					var S = p[0], ee = p[p.length - 1];
					for (x = 0; x < p.length; x += 1) jr(p[x], b, n);
					for (x = 0; x < m.length; x += 1) u.delete(m[x]);
					Mr(e, S.prev, ee.next), Mr(e, d, S), Mr(e, ee, b), l = b, d = ee, --v, p = [], m = [];
				} else u.delete(_), jr(_, l, n), Mr(e, _.prev, _.next), Mr(e, _, d === null ? e.effect.first : d.next), Mr(e, d, _), d = _;
				continue;
			}
			for (p = [], m = []; l !== null && l !== _;) (u ??= /* @__PURE__ */ new Set()).add(l), m.push(l), l = Or(l.next);
			if (l === null) continue;
		}
		_.f & 33554432 || p.push(_), d = _, l = Or(_.next);
	}
	if (e.outrogroups !== null) {
		for (let t of e.outrogroups) t.pending.size === 0 && (Tr(e, o(t.done)), e.outrogroups?.delete(t));
		e.outrogroups.size === 0 && (e.outrogroups = null);
	}
	if (l !== null || u !== void 0) {
		var C = [];
		if (u !== void 0) for (_ of u) _.f & 8192 || C.push(_);
		for (; l !== null;) !(l.f & 8192) && l !== e.fallback && C.push(l), l = Or(l.next);
		var te = C.length;
		if (te > 0) {
			var w = r & 4 && s === 0 ? n : null;
			if (a) {
				for (v = 0; v < te; v += 1) C[v].nodes?.a?.measure();
				for (v = 0; v < te; v += 1) C[v].nodes?.a?.fix();
			}
			wr(e, C, w);
		}
	}
	a && Ge(() => {
		if (f !== void 0) for (_ of f) _.nodes?.a?.apply();
	});
}
function Ar(e, t, n, r, i, a, o, s) {
	var c = o & 1 ? o & 16 ? Pt(n) : /* @__PURE__ */ Ft(n, !1, !1) : null, l = o & 2 ? Pt(i) : null;
	return {
		v: c,
		i: l,
		e: V(() => (a(t, c ?? n, l ?? i, s), () => {
			e.delete(r);
		}))
	};
}
function jr(e, t, n) {
	if (e.nodes) for (var r = e.nodes.start, i = e.nodes.end, a = t && !(t.f & 33554432) ? t.nodes.start : n; r !== null;) {
		var o = /* @__PURE__ */ Zt(r);
		if (a.before(r), r === i) return;
		r = o;
	}
}
function Mr(e, t, n) {
	t === null ? e.effect.first = n : t.next = n, n === null ? e.effect.last = t : n.prev = t;
}
//#endregion
//#region node_modules/svelte/src/internal/client/dom/css.js
function Nr(e, t) {
	pn(() => {
		e = K?.parent?.nodes?.start ?? e;
		var n = e.getRootNode(), r = n.host ? n : n.head ?? n.ownerDocument.head;
		if (!r.querySelector("#" + t.hash)) {
			let e = tn("style");
			e.id = t.hash, e.textContent = t.code, r.appendChild(e);
		}
	});
}
//#endregion
//#region node_modules/svelte/src/internal/shared/attributes.js
var Pr = [..." 	\n\r\f\xA0\v﻿"];
function Fr(e, t, n) {
	var r = e == null ? "" : "" + e;
	if (t && (r = r ? r + " " + t : t), n) {
		for (var i of Object.keys(n)) if (n[i]) r = r ? r + " " + i : i;
		else if (r.length) for (var a = i.length, o = 0; (o = r.indexOf(i, o)) >= 0;) {
			var s = o + a;
			(o === 0 || Pr.includes(r[o - 1])) && (s === r.length || Pr.includes(r[s])) ? r = (o === 0 ? "" : r.substring(0, o)) + r.substring(s + 1) : o = s;
		}
	}
	return r === "" ? null : r;
}
//#endregion
//#region node_modules/svelte/src/internal/client/dom/elements/class.js
function Ir(e, t, n, r, i, a) {
	var o = e[de];
	if (E || o !== n || o === void 0) {
		var s = Fr(n, r, a);
		(!E || s !== e.getAttribute("class")) && (s == null ? e.removeAttribute("class") : t ? e.className = s : e.setAttribute("class", s)), e[de] = n;
	} else if (a && i !== a) for (var c in a) {
		var l = !!a[c];
		(i == null || l !== !!i[c]) && e.classList.toggle(c, l);
	}
	return a;
}
//#endregion
//#region node_modules/svelte/src/internal/client/dom/elements/bindings/select.js
function Lr(e, t) {
	t ? e.hasAttribute("selected") || e.setAttribute("selected", "") : e.removeAttribute("selected");
}
function Rr(e, t) {
	var n = e.__defaultValue, i = e.multiple, a = i ? n ?? [] : null;
	if (!i || r(a)) {
		var o = e.selectedIndex, s = t && i ? new Set(e.selectedOptions) : null;
		for (var c of e.options) {
			var l = Hr(c);
			Lr(c, i ? a.includes(l) : Wt(l, n));
		}
		if (t) {
			if (s !== null) for (c of e.options) {
				var u = s.has(c);
				c.selected !== u && (c.selected = u);
			}
			else e.selectedIndex !== o && (e.selectedIndex = o);
		}
	}
}
function zr(e, t, n = !1) {
	if (e.multiple) {
		if (t == null) return;
		if (!r(t)) return ye();
		for (var i of e.options) i.selected = t.includes(Hr(i));
		return;
	}
	for (i of e.options) if (Wt(Hr(i), t)) {
		i.selected = !0;
		return;
	}
	(!n || t !== void 0) && (e.selectedIndex = -1);
}
function Br(e) {
	var t = new MutationObserver((t) => {
		t.every(Ur) || ("__defaultValue" in e && Rr(e, !1), "__value" in e && zr(e, e.__value));
	});
	t.observe(e, {
		childList: !0,
		subtree: !0,
		attributes: !0,
		attributeFilter: ["value"]
	}), ln(() => {
		t.disconnect();
	});
}
function Vr(e, t, n = t) {
	var r = /* @__PURE__ */ new WeakSet(), i = !0;
	$e(e, "change", (t) => {
		var i = t ? "[selected]" : ":checked", a;
		if (e.multiple) a = [].map.call(e.querySelectorAll(i), Hr);
		else {
			var o = e.querySelector(i) ?? e.querySelector("option:not([disabled])");
			a = o && Hr(o);
		}
		n(a), e.__value = a, M !== null && r.add(M);
	}), pn(() => {
		var a = t();
		if (e === document.activeElement) {
			var o = M;
			if (r.has(o)) return;
		}
		if (zr(e, a, i), i && a === void 0) {
			var s = e.querySelector(":checked");
			s !== null && (a = Hr(s), n(a));
		}
		e.__value = a, i = !1;
	});
}
function Hr(e) {
	return "__value" in e ? e.__value : e.value;
}
function Ur(e) {
	if (e.target.closest("selectedcontent") !== null) return !0;
	if (e.type === "childList") {
		var t = [...e.addedNodes, ...e.removedNodes];
		return t.length > 0 && t.every((e) => e.nodeName === "SELECTEDCONTENT");
	}
	return !1;
}
//#endregion
//#region node_modules/svelte/src/internal/client/dom/elements/attributes.js
var Wr = Symbol("is custom element"), Gr = Symbol("is html"), Kr = ge ? "link" : "LINK";
function qr(e) {
	if (E) {
		var t = !1, n = () => {
			if (!t) {
				if (t = !0, e.hasAttribute("value")) {
					var n = e.value;
					$(e, "value", null), e.value = n;
				}
				if (e.hasAttribute("checked")) {
					var r = e.checked;
					$(e, "checked", null), e.checked = r;
				}
			}
		};
		e[me] = n, Ge(n), Ze();
	}
}
function Jr(e, t) {
	var n = Yr(e);
	n.checked !== (n.checked = t ?? void 0) && (e.checked = t);
}
function $(e, t, n, r) {
	var i = Yr(e);
	E && (i[t] = e.getAttribute(t), t === "src" || t === "srcset" || t === "href" && e.nodeName === Kr) || i[t] !== (i[t] = n) && (t === "loading" && (e[le] = n), n == null ? e.removeAttribute(t) : typeof n != "string" && Zr(e).has(t) ? e[t] = n : e.setAttribute(t, n));
}
function Yr(e) {
	return e[ue] ??= {
		[Wr]: e.nodeName.includes("-"),
		[Gr]: e.namespaceURI === n
	};
}
var Xr = /* @__PURE__ */ new Map();
function Zr(e) {
	var t = e.getAttribute("is") || e.nodeName, n = Xr.get(t);
	if (n) return n;
	Xr.set(t, n = /* @__PURE__ */ new Set());
	for (var r, i = e, a = Element.prototype; a !== i;) {
		for (var o in r = u(i), r) r[o].set && o !== "innerHTML" && o !== "textContent" && o !== "innerText" && n.add(o);
		i = p(i);
	}
	return n;
}
//#endregion
//#region node_modules/svelte/src/internal/client/dom/elements/bindings/this.js
function Qr(e, t) {
	return e === t || e?.[T] === t;
}
function $r(e = Ve(), t, n, r) {
	var i = A.r, a = K;
	return pn(() => {
		var o, s;
		return hn(() => {
			o = s, s = r?.() || [], Xn(() => {
				Qr(n(...s), e) || (t(e, ...s), o && Qr(n(...o), e) && t(null, ...o));
			});
		}), () => {
			let r = a;
			for (; r !== i && r.parent !== null && r.parent.f & 33554432;) r = r.parent;
			let o = () => {
				s && Qr(n(...s), e) && t(null, ...s);
			}, c = r.teardown;
			r.teardown = () => {
				o(), c?.();
			};
		};
	}), e;
}
//#endregion
//#region node_modules/svelte/src/internal/client/reactivity/store.js
var ei = !1;
function ti(e) {
	var t = ei;
	try {
		return ei = !1, [e(), ei];
	} finally {
		ei = t;
	}
}
//#endregion
//#region node_modules/svelte/src/internal/client/reactivity/props.js
function ni(e, t, n, r) {
	var i = !0, a = !!(n & 8), o = !!(n & 16), s = r, c = !0, u = void 0, d = () => o && i ? (u ??= /* @__PURE__ */ it(r), X(u)) : (c && (c = !1, s = o ? Xn(r) : r), s);
	let f;
	if (a) {
		var p = T in e || ce in e;
		f = l(e, t)?.set ?? (p && t in e ? (n) => e[t] = n : void 0);
	}
	var m, h = !1;
	a ? [m, h] = ti(() => e[t]) : m = e[t], m === void 0 && r !== void 0 && (m = d(), f && (i && Ne(t), f(m)));
	var g = i ? () => {
		var n = e[t];
		return n === void 0 ? d() : (c = !0, n);
	} : () => {
		var n = e[t];
		return n !== void 0 && (s = void 0), n === void 0 ? s : n;
	};
	if (i && !(n & 4)) return g;
	if (f) {
		var _ = e.$$legacy;
		return (function(e, t) {
			return arguments.length > 0 ? ((!i || !t || _ || h) && f(t ? g() : e), e) : g();
		});
	}
	var v = !1, y = (n & 1 ? it : ct)(() => (v = !1, g()));
	a && X(y);
	var b = K;
	return (function(e, t) {
		if (arguments.length > 0) {
			let n = t ? X(y) : i && a ? Ht(e) : e;
			return F(y, n), v = !0, s !== void 0 && (s = n), e;
		}
		return kn && v || b.f & 16384 ? y.v : X(y);
	});
}
//#endregion
//#region node_modules/svelte/src/legacy/legacy-client.js
function ri(e) {
	return new ii(e);
}
var ii = class {
	#e;
	#t;
	constructor(e) {
		var t = /* @__PURE__ */ new Map(), n = (e, n) => {
			var r = /* @__PURE__ */ Ft(n, !1, !1);
			return t.set(e, r), r;
		};
		let r = new Proxy({
			...e.props || {},
			$$events: {}
		}, {
			get(e, r) {
				return X(t.get(r) ?? n(r, Reflect.get(e, r)));
			},
			has(e, r) {
				return r === ce || (X(t.get(r) ?? n(r, Reflect.get(e, r))), Reflect.has(e, r));
			},
			set(e, r, i) {
				return F(t.get(r) ?? n(r, i), i), Reflect.set(e, r, i);
			}
		});
		this.#t = (e.hydrate ? _r : gr)(e.component, {
			target: e.target,
			anchor: e.anchor,
			props: r,
			context: e.context,
			intro: e.intro ?? !1,
			recover: e.recover,
			transformError: e.transformError
		}), (!e?.props?.$$host || e.sync === !1) && wt(), this.#e = r.$$events;
		for (let e of Object.keys(this.#t)) e !== "$set" && e !== "$destroy" && e !== "$on" && c(this, e, {
			get() {
				return this.#t[e];
			},
			set(t) {
				this.#t[e] = t;
			},
			enumerable: !0
		});
		this.#t.$set = (e) => {
			Object.assign(r, e);
		}, this.#t.$destroy = () => {
			xr(this.#t);
		};
	}
	$set(e) {
		this.#t.$set(e);
	}
	$on(e, t) {
		this.#e[e] = this.#e[e] || [];
		let n = (...e) => t.call(this, ...e);
		return this.#e[e].push(n), () => {
			this.#e[e] = this.#e[e].filter((e) => e !== n);
		};
	}
	$destroy() {
		this.#t.$destroy();
	}
}, ai;
typeof HTMLElement == "function" && (ai = class extends HTMLElement {
	$$ctor;
	$$s;
	$$c;
	$$cn = !1;
	$$d = {};
	$$r = !1;
	$$p_d = {};
	$$l = {};
	$$l_u = /* @__PURE__ */ new Map();
	$$me;
	$$shadowRoot = null;
	constructor(e, t, n) {
		super(), this.$$ctor = e, this.$$s = t, n && (this.$$shadowRoot = this.attachShadow(n));
	}
	addEventListener(e, t, n) {
		if (this.$$l[e] = this.$$l[e] || [], this.$$l[e].push(t), this.$$c) {
			let n = this.$$c.$on(e, t);
			this.$$l_u.set(t, n);
		}
		super.addEventListener(e, t, n);
	}
	removeEventListener(e, t, n) {
		if (super.removeEventListener(e, t, n), this.$$c) {
			let e = this.$$l_u.get(t);
			e && (e(), this.$$l_u.delete(t));
		}
	}
	async connectedCallback() {
		if (this.$$cn = !0, !this.$$c) {
			if (await Promise.resolve(), !this.$$cn || this.$$c) return;
			function e(e) {
				return (t) => {
					let n = tn("slot");
					e !== "default" && (n.name = e), Z(t, n);
				};
			}
			let t = {}, n = si(this);
			for (let r of this.$$s) r in n && (r === "default" && !this.$$d.children ? (this.$$d.children = e(r), t.default = !0) : t[r] = e(r));
			for (let e of this.attributes) {
				let t = this.$$g_p(e.name);
				t in this.$$d || (this.$$d[t] = oi(t, e.value, this.$$p_d, "toProp"));
			}
			for (let e in this.$$p_d) !(e in this.$$d) && this[e] !== void 0 && (this.$$d[e] = this[e], delete this[e]);
			this.$$c = ri({
				component: this.$$ctor,
				target: this.$$shadowRoot || this,
				props: {
					...this.$$d,
					$$slots: t,
					$$host: this
				}
			}), this.$$me = dn(() => {
				hn(() => {
					this.$$r = !0;
					for (let e of s(this.$$c)) {
						if (!this.$$p_d[e]?.reflect) continue;
						this.$$d[e] = this.$$c[e];
						let t = oi(e, this.$$d[e], this.$$p_d, "toAttribute");
						t == null ? this.removeAttribute(this.$$p_d[e].attribute || e) : this.setAttribute(this.$$p_d[e].attribute || e, t);
					}
					this.$$r = !1;
				});
			});
			for (let e in this.$$l) for (let t of this.$$l[e]) {
				let n = this.$$c.$on(e, t);
				this.$$l_u.set(t, n);
			}
			this.$$l = {};
		}
	}
	attributeChangedCallback(e, t, n) {
		this.$$r || (e = this.$$g_p(e), this.$$d[e] = oi(e, n, this.$$p_d, "toProp"), this.$$c?.$set({ [e]: this.$$d[e] }));
	}
	disconnectedCallback() {
		this.$$cn = !1, Promise.resolve().then(() => {
			!this.$$cn && this.$$c && (this.$$c.$destroy(), this.$$me(), this.$$c = void 0);
		});
	}
	$$g_p(e) {
		return s(this.$$p_d).find((t) => this.$$p_d[t].attribute === e || !this.$$p_d[t].attribute && t.toLowerCase() === e) || e;
	}
});
function oi(e, t, n, r) {
	let i = n[e]?.type;
	if (t = i === "Boolean" && typeof t != "boolean" ? t != null : t, !r || !n[e]) return t;
	if (r === "toAttribute") switch (i) {
		case "Object":
		case "Array": return t == null ? null : JSON.stringify(t);
		case "Boolean": return t ? "" : null;
		case "Number": return t ?? null;
		default: return t;
	}
	else switch (i) {
		case "Object":
		case "Array": return t && JSON.parse(t);
		case "Boolean": return t;
		case "Number": return t == null ? t : +t;
		default: return t;
	}
}
function si(e) {
	let t = {};
	return e.childNodes.forEach((e) => {
		t[e.slot || "default"] = !0;
	}), t;
}
function ci(e, t, n, r, i, a) {
	let o = class extends ai {
		constructor() {
			super(e, n, i), this.$$p_d = t;
		}
		static get observedAttributes() {
			return s(t).map((e) => (t[e].attribute || e).toLowerCase());
		}
	};
	return s(t).forEach((e) => {
		c(o.prototype, e, {
			get() {
				return this.$$c && e in this.$$c ? this.$$c[e] : this.$$d[e];
			},
			set(n) {
				n = oi(e, n, t), this.$$d[e] = n;
				var r = this.$$c;
				r && (l(r, e)?.get ? r[e] = n : r.$set({ [e]: n }));
			}
		});
	}), r.forEach((e) => {
		c(o.prototype, e, { get() {
			return this.$$c?.[e];
		} });
	}), a && (o = a(o)), e.element = o, o;
}
//#endregion
//#region src/CutReview.svelte
var li = /* @__PURE__ */ lr("<h3 class=\"storyboard-chapter svelte-53bf4b\"> </h3>"), ui = /* @__PURE__ */ lr("<!> <button><span class=\"shot-position svelte-53bf4b\"><span class=\"svelte-53bf4b\"> </span><span class=\"svelte-53bf4b\"> </span></span> <span class=\"shot-image svelte-53bf4b\"><img loading=\"lazy\" class=\"svelte-53bf4b\"/></span> <span class=\"shot-meta svelte-53bf4b\"><span> </span><span class=\"svelte-53bf4b\"> </span></span> <span class=\"storyboard-story svelte-53bf4b\"> </span> <span class=\"shot-status svelte-53bf4b\"> </span></button>", 1), di = /* @__PURE__ */ lr("<p class=\"empty-review svelte-53bf4b\"> </p>"), fi = /* @__PURE__ */ lr("<h4 class=\"svelte-53bf4b\"> </h4><p class=\"svelte-53bf4b\"> </p>", 1), pi = /* @__PURE__ */ lr("<p class=\"svelte-53bf4b\"> </p>"), mi = /* @__PURE__ */ lr("<h4 class=\"svelte-53bf4b\"> </h4> <img class=\"alternative-image svelte-53bf4b\" loading=\"lazy\"/> <p class=\"svelte-53bf4b\"> </p>", 1), hi = /* @__PURE__ */ lr("<button class=\"svelte-53bf4b\"> </button>"), gi = /* @__PURE__ */ lr("<label class=\"include-picture svelte-53bf4b\"><input type=\"checkbox\" class=\"svelte-53bf4b\"/> </label> <p class=\"selection-note svelte-53bf4b\"> </p> <!>", 1), _i = /* @__PURE__ */ lr("<aside class=\"picture-inspector svelte-53bf4b\"><img class=\"inspector-image svelte-53bf4b\"/> <div class=\"inspector-body svelte-53bf4b\"><div class=\"shot-meta svelte-53bf4b\"><span class=\"svelte-53bf4b\"> </span><span class=\"svelte-53bf4b\"> </span></div> <h3 class=\"svelte-53bf4b\"> </h3> <h4 class=\"svelte-53bf4b\"> </h4> <p class=\"svelte-53bf4b\"> </p> <!> <!> <!> <!> <div class=\"review-controls svelte-53bf4b\"><!> <button class=\"svelte-53bf4b\"> </button> <button class=\"svelte-53bf4b\"> </button> <button class=\"back-to-pictures svelte-53bf4b\"> </button></div></div></aside>"), vi = /* @__PURE__ */ lr("<div class=\"cut-review svelte-53bf4b\"><div class=\"review-toolbar svelte-53bf4b\"><p class=\"svelte-53bf4b\"> </p> <label class=\"svelte-53bf4b\"> <select class=\"svelte-53bf4b\"><option class=\"svelte-53bf4b\"> </option><option class=\"svelte-53bf4b\"> </option><option class=\"svelte-53bf4b\"> </option><option class=\"svelte-53bf4b\"> </option></select></label></div> <div class=\"review-layout svelte-53bf4b\"><div class=\"contact-sheet svelte-53bf4b\"></div> <!></div></div>"), yi = {
	hash: "svelte-53bf4b",
	code: ".cut-views, .cut-views > .q-panel {overflow:visible;}.cut-review.svelte-53bf4b {color:var(--im-text);font:inherit;width:100%;}.cut-review.svelte-53bf4b p:where(.svelte-53bf4b), .cut-review.svelte-53bf4b h3:where(.svelte-53bf4b), .cut-review.svelte-53bf4b h4:where(.svelte-53bf4b) {margin:0;}.review-toolbar.svelte-53bf4b {display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:16px;font-size:12px;color:var(--im-text-secondary);}.review-toolbar.svelte-53bf4b label:where(.svelte-53bf4b) {display:flex;align-items:center;gap:8px;flex-shrink:0;}.cut-review.svelte-53bf4b select:where(.svelte-53bf4b), .review-controls.svelte-53bf4b button:where(.svelte-53bf4b) {background:var(--im-bg-elevated);color:var(--im-text);border:1px solid var(--im-border-hover);border-radius:6px;padding:8px 10px;font:inherit;cursor:pointer;}.review-layout.svelte-53bf4b {display:grid;grid-template-columns:minmax(0, 1fr) 320px;gap:24px;align-items:start;}.contact-sheet.svelte-53bf4b {display:grid;grid-template-columns:repeat(auto-fill, minmax(190px, 1fr));gap:18px 16px;align-items:start;}.storyboard-chapter.svelte-53bf4b, .empty-review.svelte-53bf4b {grid-column:1 / -1;font-size:14px;font-weight:600;}.storyboard-shot.svelte-53bf4b {min-width:0;display:flex;flex-direction:column;gap:6px;text-align:left;border:2px solid transparent;border-radius:8px;padding:7px;background:transparent;color:inherit;font:inherit;cursor:pointer;}.storyboard-shot.svelte-53bf4b:hover {background:var(--im-bg-surface);}.storyboard-shot.active.svelte-53bf4b {border-color:var(--im-primary);}.shot-position.svelte-53bf4b, .shot-meta.svelte-53bf4b {display:flex;align-items:center;justify-content:space-between;gap:8px;width:100%;font-size:11px;font-variant-numeric:tabular-nums;color:var(--im-text-secondary);}.shot-position.svelte-53bf4b {font-weight:600;}.shot-image.svelte-53bf4b {display:flex;width:100%;aspect-ratio:4 / 3;background:var(--im-bg-surface);border-radius:4px;overflow:hidden;}.shot-image.svelte-53bf4b img:where(.svelte-53bf4b) {width:100%;height:100%;object-fit:contain;}.storyboard-story.svelte-53bf4b {font-size:13px;font-weight:600;line-height:1.4;}.shot-status.svelte-53bf4b {font-size:11px;color:var(--im-text-secondary);}.excluded.svelte-53bf4b .shot-image:where(.svelte-53bf4b) {opacity:.45;}.excluded.svelte-53bf4b .shot-status:where(.svelte-53bf4b) {color:var(--im-warning-text);}.picture-inspector.svelte-53bf4b {position:sticky;top:24px;max-height:calc(100dvh - 48px);border:1px solid var(--im-border);border-radius:10px;overflow:auto;background:var(--im-bg-surface);}.inspector-image.svelte-53bf4b {display:block;width:100%;aspect-ratio:4 / 3;object-fit:contain;background:var(--im-bg);}.inspector-body.svelte-53bf4b {padding:18px;}.inspector-body.svelte-53bf4b h3:where(.svelte-53bf4b) {font-size:17px;line-height:1.4;font-weight:600;margin:10px 0 20px;}.inspector-body.svelte-53bf4b h4:where(.svelte-53bf4b) {font-size:12px;font-weight:600;margin:18px 0 6px;}.inspector-body.svelte-53bf4b p:where(.svelte-53bf4b) {font-size:13px;line-height:1.6;overflow-wrap:anywhere;}.alternative-image.svelte-53bf4b {width:100%;max-height:140px;object-fit:contain;margin:4px 0;}.review-controls.svelte-53bf4b {display:flex;flex-direction:column;gap:10px;border-top:1px solid var(--im-border);margin-top:20px;padding-top:18px;}.include-picture.svelte-53bf4b {display:flex;align-items:center;gap:10px;font-size:13px;font-weight:600;cursor:pointer;}.include-picture.svelte-53bf4b input:where(.svelte-53bf4b) {width:17px;height:17px;accent-color:var(--im-primary);}.review-controls.svelte-53bf4b .selection-note:where(.svelte-53bf4b) {font-size:11px;color:var(--im-text-secondary);}.review-controls.svelte-53bf4b button:where(.svelte-53bf4b) {text-align:left;font-size:12px;}.back-to-pictures.svelte-53bf4b {display:none;}.cut-review.svelte-53bf4b :where(.svelte-53bf4b):focus-visible {outline:3px solid var(--im-primary);outline-offset:3px;}\n  @media (max-width: 1050px) {.review-layout.svelte-53bf4b {grid-template-columns:minmax(0, 1fr) 280px;gap:16px;}.contact-sheet.svelte-53bf4b {grid-template-columns:repeat(auto-fill, minmax(150px, 1fr));gap:10px;} }\n  @media (max-width: 700px) {.review-layout.svelte-53bf4b {grid-template-columns:1fr;}.picture-inspector.svelte-53bf4b {position:static;grid-row:1;max-height:none;}.inspector-image.svelte-53bf4b {max-height:220px;}.review-toolbar.svelte-53bf4b {align-items:flex-start;flex-direction:column;}.back-to-pictures.svelte-53bf4b {display:block;} }"
};
function bi(e, t) {
	ze(t, !0), Nr(e, yi);
	let n = ni(t, "payload", 7, "{\"shots\":[],\"editable\":false,\"labels\":{}}"), r = /* @__PURE__ */ st(() => JSON.parse(n())), i = /* @__PURE__ */ st(() => X(r).labels), a = /* @__PURE__ */ P(""), o = /* @__PURE__ */ P("all"), s = /* @__PURE__ */ P(void 0), c, l = /* @__PURE__ */ st(() => X(r).shots.filter((e) => X(o) === "all" || X(o) === "motion" && e.motion || X(o) === "stills" && !e.motion || X(o) === "excluded" && !e.included)), u = /* @__PURE__ */ st(() => X(l).find((e) => e.asset_id === X(a)) ?? X(l)[0]), d = (e, t = !1) => `/media/thumb/${encodeURIComponent(e)}${t ? "?size=preview" : ""}`;
	function f(e, n, r) {
		t.$$host.dispatchEvent(new CustomEvent("review-action", { detail: {
			action: e,
			asset_id: n,
			included: r
		} }));
	}
	async function p(e) {
		F(a, e, !0), await qn(), matchMedia("(max-width: 700px)").matches && X(s)?.scrollIntoView({ block: "start" });
	}
	var m = {
		get payload() {
			return n();
		},
		set payload(e = "{\"shots\":[],\"editable\":false,\"labels\":{}}") {
			n(e), wt();
		}
	}, h = vi(), g = L(h), _ = L(g), v = R(_, !0), y = z(_, 2), b = L(y), x = z(b), S = L(x), ee = R(S, !0);
	S.value = S.__value = "all";
	var C = z(S), te = R(C, !0);
	C.value = C.__value = "motion";
	var w = z(C), ne = R(w, !0);
	w.value = w.__value = "stills";
	var re = z(w), ie = R(re, !0);
	re.value = re.__value = "excluded", k(x), Br(x), k(y), k(g);
	var ae = z(g, 2), oe = L(ae);
	Dr(oe, 21, () => X(l), (e) => e.asset_id, (e, t) => {
		var n = ui(), a = Qt(n), o = (e) => {
			var n = li(), r = R(n, !0);
			B(() => Q(r, X(t).chapter)), Z(e, n);
		};
		Cr(a, (e) => {
			X(t).chapter && e(o);
		});
		var s = z(a, 2);
		let c;
		var l = L(s), f = L(l), m = R(f, !0), h = R(z(f), !0);
		k(l);
		var g = z(l, 2), _ = R(g), v = z(g, 2), y = L(v);
		let b;
		var x = R(y, !0), S = R(z(y));
		k(v);
		var ee = z(v, 2), C = R(ee, !0), te = R(z(ee, 2));
		k(s), B((e, n, r) => {
			c = Ir(s, 1, "storyboard-shot svelte-53bf4b", null, c, {
				active: X(u)?.asset_id === X(t).asset_id,
				excluded: !X(t).included
			}), $(s, "aria-pressed", X(u)?.asset_id === X(t).asset_id), Q(m, e), Q(h, X(t).timecode), $(_, "src", n), $(_, "alt", X(t).moment || X(t).story_title), b = Ir(y, 1, "svelte-53bf4b", null, b, { "storyboard-day": X(t).new_day }), Q(x, X(t).day), Q(S, `${r ?? ""} s`), Q(C, X(t).story_title), Q(te, `${X(t).kind_label ?? ""}${X(t).included ? "" : ` / ${X(i).excluded}`}`);
		}, [
			() => String(X(r).shots.indexOf(X(t)) + 1).padStart(2, "0"),
			() => d(X(t).asset_id),
			() => X(t).seconds.toFixed(1)
		]), er("click", s, () => p(X(t).asset_id)), Z(e, n);
	}, (e) => {
		var t = di(), n = R(t, !0);
		B(() => Q(n, X(i).noPictures)), Z(e, t);
	}), k(oe), $r(oe, (e) => c = e, () => c);
	var T = z(oe, 2), se = (e) => {
		var t = _i(), n = L(t), a = z(n, 2), o = L(a), l = L(o), p = R(l, !0), m = R(z(l));
		k(o);
		var h = z(o, 2), g = R(h, !0), _ = z(h, 2), v = R(_, !0), y = z(_, 2), b = R(y, !0), x = z(y, 2), S = (e) => {
			var t = fi(), n = Qt(t), r = R(n, !0), a = R(z(n), !0);
			B(() => {
				Q(r, X(i).modelSuggestion), Q(a, X(u).decision.model_reason);
			}), Z(e, t);
		};
		Cr(x, (e) => {
			X(u).decision?.model_reason && e(S);
		});
		var ee = z(x, 2), C = (e) => {
			var t = fi(), n = Qt(t), r = R(n, !0), a = R(z(n), !0);
			B(() => {
				Q(r, X(i).keptBecause), Q(a, X(u).decision.kept_reason);
			}), Z(e, t);
		};
		Cr(ee, (e) => {
			X(u).decision?.kept_reason && e(C);
		});
		var te = z(ee, 2), w = (e) => {
			var t = pi(), n = R(t);
			B(() => Q(n, `${X(i).alternativeCount ?? ""}: ${X(u).decision.offered_count ?? ""}`)), Z(e, t);
		};
		Cr(te, (e) => {
			X(u).decision && X(u).decision.offered_count > 0 && e(w);
		});
		var ne = z(te, 2), re = (e) => {
			var t = mi(), n = Qt(t), r = R(n, !0), a = z(n, 2), o = R(z(a, 2), !0);
			B((e) => {
				Q(r, X(i).alternative), $(a, "src", e), $(a, "alt", X(i).alternative), Q(o, X(u).decision.replacement_outcome || X(i).noOutcome);
			}, [() => d(X(u).decision.proposed_asset_id)]), Z(e, t);
		};
		Cr(ne, (e) => {
			X(u).decision?.proposed_asset_id && e(re);
		});
		var ie = z(ne, 2), ae = L(ie), oe = (e) => {
			var t = gi(), n = Qt(t), r = L(n);
			qr(r);
			var a = z(r, 1, !0);
			k(n);
			var o = z(n, 2), s = R(o, !0), c = z(o, 2), l = (e) => {
				var t = hi(), n = R(t, !0);
				B(() => Q(n, X(i).trim)), er("click", t, () => f("trim", X(u).asset_id)), Z(e, t);
			};
			Cr(c, (e) => {
				X(u).motion && e(l);
			}), B(() => {
				Jr(r, X(u).included), Q(a, X(i).include), Q(s, X(i).selectionNote);
			}), er("change", r, (e) => f("include", X(u).asset_id, e.currentTarget.checked)), Z(e, t);
		};
		Cr(ae, (e) => {
			X(r).editable && e(oe);
		});
		var T = z(ae, 2), se = R(T, !0), ce = z(T, 2), le = R(ce, !0), ue = z(ce, 2), de = R(ue, !0);
		k(ie), k(a), k(t), $r(t, (e) => F(s, e), () => X(s)), B((e, r) => {
			$(t, "aria-label", X(i).pictureReview), $(n, "src", e), $(n, "alt", X(u).moment || X(u).story_title), Q(p, X(u).day), Q(m, `${X(u).timecode ?? ""} / ${r ?? ""} s`), Q(g, X(u).story_title), Q(v, X(i).why), Q(b, X(u).reason || X(i).noReason), Q(se, X(i).decisions), Q(le, X(i).alternatives), Q(de, X(i).backToPictures);
		}, [() => d(X(u).asset_id, !0), () => X(u).seconds.toFixed(1)]), er("click", T, () => f("decisions", X(u).asset_id)), er("click", ce, () => f("pool", X(u).asset_id)), er("click", ue, () => c.scrollIntoView({ block: "start" })), Z(e, t);
	};
	return Cr(T, (e) => {
		X(u) && e(se);
	}), k(ae), k(h), B(() => {
		Q(v, X(i).savedTiming), Q(b, `${X(i).show ?? ""} `), Q(ee, X(i).all), Q(te, X(i).videos), Q(ne, X(i).stills), Q(ie, X(i).excluded), $(oe, "aria-label", X(i).contactSheet);
	}), Vr(x, () => X(o), (e) => F(o, e)), Z(e, h), Be(m);
}
tr(["click", "change"]), customElements.define("im-cut-review", ci(bi, { payload: {} }, [], []));
//#endregion
export { bi as default };
