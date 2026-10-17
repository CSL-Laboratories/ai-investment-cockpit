//! CPython 3.12 `random.Random` for integer seeds.
//!
//! The synthetic feed's stream is defined by that generator, including
//! `gauss`'s cached second sample. Seeding follows `_random.Random.seed` for a
//! Python int: the absolute value, little-endian 32-bit limbs, `init_by_array`.
//!
//! `sin`, `cos`, and `ln` go through the pure-Rust `libm` crate. `f64::sin`,
//! `f64::cos`, and `f64::ln` call the platform libm, and Rust does not promise
//! those results match across platforms or versions. A one-ulp change in the
//! raw sample, scaled by `tick_volatility` or by a large `exchange_gap_ns`,
//! can move `round` or the gap cut. `box_muller` and `unit_exponential` are
//! the kernel both this generator and the Python reference call, so the two
//! tapes stay on the same side of those cuts for every parameter. Platform
//! math remains only when the extension is not installed.

const N: usize = 624;
const M: usize = 397;
const MATRIX_A: u32 = 0x9908_b0df;
const UPPER_MASK: u32 = 0x8000_0000;
const LOWER_MASK: u32 = 0x7fff_ffff;

pub struct Mt {
    state: [u32; N],
    index: usize,
    gauss_next: Option<f64>,
}

impl Mt {
    pub fn from_seed(seed: i64) -> Self {
        let mut mt = Self {
            state: [0; N],
            index: N,
            gauss_next: None,
        };
        mt.seed(seed);
        mt
    }

    fn seed(&mut self, seed: i64) {
        let n = seed.unsigned_abs();
        if n <= u64::from(u32::MAX) {
            self.init_by_array(&[n as u32]);
        } else {
            self.init_by_array(&[n as u32, (n >> 32) as u32]);
        }
        self.gauss_next = None;
    }

    fn init_genrand(&mut self, seed: u32) {
        self.state[0] = seed;
        for index in 1..N {
            let prev = self.state[index - 1];
            self.state[index] = 1_812_433_253u32
                .wrapping_mul(prev ^ (prev >> 30))
                .wrapping_add(index as u32);
        }
        self.index = N;
    }

    fn init_by_array(&mut self, key: &[u32]) {
        self.init_genrand(19_650_218);
        let mut i = 1usize;
        let mut j = 0usize;
        let mut k = N.max(key.len());
        while k > 0 {
            let prev = self.state[i - 1];
            self.state[i] = (self.state[i] ^ (prev ^ (prev >> 30)).wrapping_mul(1_664_525))
                .wrapping_add(key[j])
                .wrapping_add(j as u32);
            i += 1;
            j += 1;
            if i >= N {
                self.state[0] = self.state[N - 1];
                i = 1;
            }
            if j >= key.len() {
                j = 0;
            }
            k -= 1;
        }
        k = N - 1;
        while k > 0 {
            let prev = self.state[i - 1];
            self.state[i] = (self.state[i] ^ (prev ^ (prev >> 30)).wrapping_mul(1_566_083_941))
                .wrapping_sub(i as u32);
            i += 1;
            if i >= N {
                self.state[0] = self.state[N - 1];
                i = 1;
            }
            k -= 1;
        }
        self.state[0] = UPPER_MASK;
    }

    fn genrand_uint32(&mut self) -> u32 {
        if self.index >= N {
            for kk in 0..(N - M) {
                let y = (self.state[kk] & UPPER_MASK) | (self.state[kk + 1] & LOWER_MASK);
                let mag = if y & 1 == 0 { 0 } else { MATRIX_A };
                self.state[kk] = self.state[kk + M] ^ (y >> 1) ^ mag;
            }
            for kk in (N - M)..(N - 1) {
                let y = (self.state[kk] & UPPER_MASK) | (self.state[kk + 1] & LOWER_MASK);
                let mag = if y & 1 == 0 { 0 } else { MATRIX_A };
                self.state[kk] = self.state[kk - (N - M)] ^ (y >> 1) ^ mag;
            }
            let y = (self.state[N - 1] & UPPER_MASK) | (self.state[0] & LOWER_MASK);
            let mag = if y & 1 == 0 { 0 } else { MATRIX_A };
            self.state[N - 1] = self.state[M - 1] ^ (y >> 1) ^ mag;
            self.index = 0;
        }
        let mut y = self.state[self.index];
        self.index += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^= y >> 18;
        y
    }

    pub fn getrandbits(&mut self, mut k: u32) -> u64 {
        if k == 0 {
            return 0;
        }
        if k <= 32 {
            return u64::from(self.genrand_uint32() >> (32 - k));
        }
        let words = ((k - 1) / 32 + 1) as usize;
        let mut out = 0u64;
        for i in 0..words {
            let mut r = self.genrand_uint32();
            if k < 32 {
                r >>= 32 - k;
            }
            out |= u64::from(r) << (32 * i);
            k = k.saturating_sub(32);
        }
        out
    }

    pub fn random(&mut self) -> f64 {
        let a = f64::from(self.genrand_uint32() >> 5);
        let b = f64::from(self.genrand_uint32() >> 6);
        (a * 67_108_864.0 + b) * (1.0 / 9_007_199_254_740_992.0)
    }

    pub fn randbelow(&mut self, n: u64) -> u64 {
        let k = 64 - n.leading_zeros();
        loop {
            let r = self.getrandbits(k);
            if r < n {
                return r;
            }
        }
    }

    pub fn randrange(&mut self, start: i64, stop: i64, step: i64) -> Option<i64> {
        let width = stop.checked_sub(start)?;
        if step == 1 {
            if width > 0 {
                return Some(start + self.randbelow(width as u64) as i64);
            }
            return None;
        }
        if step == 0 {
            return None;
        }
        let n = if step > 0 {
            (width + step - 1).div_euclid(step)
        } else {
            (width + step + 1).div_euclid(step)
        };
        if n <= 0 {
            return None;
        }
        Some(start + step * self.randbelow(n as u64) as i64)
    }

    pub fn randint(&mut self, a: i64, b: i64) -> Option<i64> {
        self.randrange(a, b.checked_add(1)?, 1)
    }

    pub fn gauss(&mut self, mu: f64, sigma: f64) -> f64 {
        let z = if let Some(cached) = self.gauss_next.take() {
            cached
        } else {
            let (z, next) = box_muller(self.random(), self.random());
            self.gauss_next = Some(next);
            z
        };
        mu + z * sigma
    }

    pub fn expovariate(&mut self, lambd: f64) -> f64 {
        unit_exponential(self.random()) / lambd
    }
}

/// CPython's Box-Muller pair. The first value is `cos` (returned); the second
/// is `sin` (cached). Uniforms are consumed angle, then radius.
pub fn box_muller(u_angle: f64, u_radius: f64) -> (f64, f64) {
    let x2pi = u_angle * std::f64::consts::TAU;
    let g2rad = (-2.0 * libm::log(1.0 - u_radius)).sqrt();
    (libm::cos(x2pi) * g2rad, libm::sin(x2pi) * g2rad)
}

/// `-ln(1 - u)`, the numerator of CPython's `expovariate`.
pub fn unit_exponential(u: f64) -> f64 {
    -libm::log(1.0 - u)
}

#[cfg(test)]
mod tests {
    use super::{box_muller, Mt};

    #[test]
    fn random_matches_cpython_random_of_seed_3() {
        let mut mt = Mt::from_seed(3);
        let bits = [
            4597741599537722632,
            4603077200617770381,
            4600336139718744798,
            4603614847666843210,
        ];
        for expected in bits {
            assert_eq!(mt.random().to_bits(), expected);
        }
    }

    #[test]
    fn getrandbits_matches_cpython_for_seed_3() {
        let mut mt = Mt::from_seed(3);
        let samples = [
            (1, 0u64),
            (17, 77678),
            (17, 71333),
            (32, 560161641),
            (40, 1006611292580),
        ];
        for (k, expected) in samples {
            assert_eq!(mt.getrandbits(k), expected);
        }
    }

    #[test]
    fn gauss_locks_the_portable_libm_stream_for_seed_3() {
        let mut mt = Mt::from_seed(3);
        // libm 0.2.15, the version Cargo.lock pins. The fourth sample is one
        // ulp under CPython's platform libm on glibc; the other three match.
        let portable = [
            4584937924398319004u64,
            4601553072989498596,
            13822916034150412811,
            4599928597848966274,
        ];
        let cpython_glibc = [
            4584937924398319004u64,
            4601553072989498596,
            13822916034150412811,
            4599928597848966275,
        ];
        for (&got, python) in portable.iter().zip(cpython_glibc) {
            assert!(got.abs_diff(python) <= 1, "{got} vs {python}");
        }
        for expected in portable {
            assert_eq!(mt.gauss(0.0, 0.35).to_bits(), expected);
        }
    }

    #[test]
    fn box_muller_is_the_pair_gauss_returns_then_caches() {
        let mut mt = Mt::from_seed(3);
        let (z, next) = box_muller(mt.random(), mt.random());
        let mut again = Mt::from_seed(3);
        assert_eq!(again.gauss(0.0, 1.0).to_bits(), z.to_bits());
        assert_eq!(again.gauss(0.0, 1.0).to_bits(), next.to_bits());
    }
}
