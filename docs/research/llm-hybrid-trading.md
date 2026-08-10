---
title: Viability Assessment: LLM-Extraction + Deterministic-Algorithm Hybrid for Capacity-Constrained Equity Niches
type: research-report
status: reference
---
# Viability Assessment: LLM-Extraction + Deterministic-Algorithm Hybrid for Capacity-Constrained Equity Niches

## TL;DR
- **The architecture is defensible in principle but the edge is thin and shrinking.** Confining the LLM to a text-to-structured-data extraction layer is the single best design choice available: extraction is where LLMs are demonstrably strong (fine-tuned/prompted F1 in the 0.74–0.94 range on financial NER) and where look-ahead contamination is far less dangerous than in the return-prediction studies that have repeatedly collapsed out-of-sample. But the target anomalies have measurably decayed, and microcap transaction costs are large enough to consume most of the theoretical spread for a small operator.
- **Transaction costs, not model quality, are the binding constraint.** The typical US microcap trades only ~$200K–$325K per day; smallest-decile round-trip costs have historically run ~10.3% (Lesmond, Ogden & Trzcinka 1999), and a $500K position is 1.5–2.5× an entire day's volume. Below roughly $50K–$100K per position the strategy can clear costs in the most liquid event names; above a few hundred thousand dollars per name it stops being viable in true microcaps.
- **Do it as a research program, not a business — and keep it your own capital.** Trading your own money needs no registration; the moment you manage others' money or sell individualized signals you cross bright regulatory lines. The decisive empirical tests are (a) strict post-training-cutoff extraction accuracy audits and (b) live paper-trading with realistic fills, because backtests here are systematically optimistic.

## Key Findings

1. **Extraction is the right job for the LLM.** On the FiNER-ORD financial NER benchmark (Lu & Huo, FinNLP 2025, peer-reviewed workshop paper), GPT-4o with in-context learning reached a weighted F1 of ~0.82, LLaMA-3.1-70B ~0.83, and Gemini-1.5 ~0.84, while fine-tuned BERT/RoBERTa scored ~0.87–0.88. Organization entities — the ones that matter for deal parsing — were the weakest category (ORG F1 ~0.70–0.80). An extraction layer is usable but requires validation and cannot be assumed error-free, especially on organization names and nested entities.

2. **Direct return prediction by LLMs is contaminated and fragile.** The Lopez-Lira & Tang (2023, revised 2025) "Can ChatGPT Forecast Stock Price Movements?" result — approximately 90% portfolio-day hit rates for the non-tradable initial reaction, stronger for small stocks — is the headline claim, but multiple independent studies show the effect substantially reflects memorization. Benhenda's Look-Ahead-Bench (2026 preprint) found strategies that returned +20.7% (DeepSeek) and +13.8% (Llama 3.1) annualized alpha in-sample collapsed to −1.0% and −3.4% out-of-sample. This is the strongest reason to keep the LLM away from forecasting.

3. **The target anomalies have decayed sharply and are documented to do so.** The S&P 500 index-inclusion effect fell from an average of 7.4% (1990s) to 0.3% over the past decade; merger-arb spreads fell >400bp since 2002; McLean & Pontiff (2016, Journal of Finance) show portfolio returns are 58% lower post-publication.

4. **Transaction costs dominate for a small operator in microcaps.** Median microcap ADV is ~$200K–$325K/day; smallest-decile historical round-trip cost ~10.3% (Lesmond, Ogden & Trzcinka 1999).

5. **Data infrastructure is the second binding constraint.** True point-in-time, survivorship-bias-free data (CRSP, Compustat Point-in-Time) is priced for institutions; free/cheap sources carry survivorship and restatement bias.

6. **Regulation is permissive for own-capital trading and restrictive the moment others are involved.**

## Details

### 1. Evidence base for LLM financial text processing

**Extraction (the proposed use).** The most directly relevant peer-reviewed evidence is Lu & Huo, "Financial Named Entity Recognition: How Far Can LLM Go?" (FinNLP 2025 workshop, ACL Anthology, pages 164–168). Using the FiNER-ORD benchmark (201 annotated financial news articles), they tested GPT-4o, LLaMA-3.1, and Gemini-1.5 under direct, in-context (5-shot), and chain-of-thought prompting. Results (entity-level and weighted F1):
- Fine-tuned BERT: weighted F1 0.8744; RoBERTa: 0.8792 (these are the ceiling).
- Best LLMs with in-context learning: Gemini 0.8368, LLaMA-70B 0.8321, GPT-4o 0.8203.
- Persons (PER) extracted well (F1 ~0.92–0.94); organizations (ORG) worst (~0.68–0.80).

Category: **peer-reviewed workshop paper.** The key limitation for this use case: FiNER-ORD is news text, not merger agreements or bankruptcy dockets, so these numbers are an optimistic proxy for performance on dense legal/financial documents. Vendor tools (LlamaExtract, LangExtract, sec-api, EdgarTools) exist and demonstrate feasibility of schema-based extraction from 10-Ks with source grounding/citations, but their accuracy claims are **vendor marketing**, not independently benchmarked.

**Direct return prediction (the use being avoided).** Lopez-Lira & Tang (2023; first version April 6, 2023, revised October 2025), "Can ChatGPT Forecast Stock Price Movements? Return Predictability and Large Language Models," SSRN 4412788 / arXiv 2304.07619. Category: **influential preprint / working paper** (widely cited, presented at the SEC and Bloomberg, but not yet a peer-reviewed journal article in final form). The abstract states verbatim: "Using post-knowledge-cutoff headlines, GPT-4 captures initial market responses, achieving approximately 90% portfolio-day hit rates for the non-tradable initial reaction." Reported daily hit rates are 93.3% (overnight news) and 88.8% (intraday); the long-short overnight-drift strategy shows an annualized Sharpe of ~3.8 (GPT-4) vs ~3.1 (GPT-3.5), **before costs**. GPT-4 scores predict subsequent drift most strongly for small stocks and negative news, and the authors note strategy returns decline as LLM adoption rises.

**Strongest methodological criticisms:**
- Glasserman & Lin, "Assessing Look-Ahead Bias in Stock Return Predictions Generated By GPT Sentiment Analysis" (arXiv 2309.17322; published in *The Journal of Financial Data Science* 6(1):25–42, peer-reviewed). Decompose bias into look-ahead and a "distraction effect"; propose anonymization.
- Sarkar & Vafa, "Lookahead Bias in Pretrained Language Models" (SSRN 4754678, June 2024; ICML 2025 workshop). Direct tests find look-ahead bias in earnings-call risk-factor prediction and election prediction; conclude prompting-based mitigations do NOT eliminate it and that only chronologically-consistent pretraining fully addresses it.
- Gao, Jiang & Yan, "Detecting Lookahead Bias in LLM Forecasts" (arXiv 2512.23847, NBER working paper). Introduce "Lookahead Propensity" (LAP); the LLM×LAP interaction that is significant in-sample "loses significance on post-training-cutoff samples."
- Benhenda, "Look-Ahead-Bench" (arXiv 2601.13770, 2026 preprint). Documented the in-sample→out-of-sample collapse and a "Scaling Paradox": the largest model (DeepSeek, 671B parameters) had the worst alpha decay.
- He, Lv, Manela & Wu, "Chronologically Consistent Large Language Models" (ChronoBERT/ChronoGPT, arXiv 2502.21206). Note dissenting evidence: they find look-ahead bias in return prediction is "modest" and application-specific — an important nuance.

### 2. Look-ahead contamination

Proposed controls and how well they work:
- **Restricting to post-cutoff data** — works by construction but the out-of-sample window is short, limiting statistical power.
- **Entity anonymization** (Glasserman & Lin) — reduces bias but degrades semantics; surprisingly, anonymized headlines outperformed in-sample, revealing the "distraction effect."
- **Chronologically-consistent models** (ChronoBERT/ChronoGPT; Point-in-Time models) — mitigate by construction but require heavy curation/retraining.
- **Prompt engineering/masking** — Sarkar & Vafa show these are insufficient.

Documented collapses: Benhenda (2026) is the clearest — matched in/out-of-sample periods with similar market returns (~25% both) isolate bias, and standard LLM strategies swung from double-digit positive alpha to negative. MemGuard-Alpha (arXiv 2603.26797) reports the same LLMs returned >44% in-sample with ~22pp decay for DeepSeek post-cutoff.

**Consensus:** There is emerging consensus that (a) look-ahead bias is real and can be large in return prediction; (b) it is task- and model-specific; (c) prompting fixes are inadequate; (d) the gold standard is either post-cutoff-only evaluation or chronologically-consistent models. There is NO consensus on a single validation protocol, and a systematic review found no bias type studied at >28% incidence across 164 papers. **Crucially for this architecture: these contamination findings apply to prediction, not to extraction of facts explicitly present in a document — which materially strengthens the case for the extraction-only design.**

### 3. Target niches — base rates and decay

- **Merger arbitrage.** Jetley & Ji (2010, *Financial Analysts Journal* 66(2), peer-reviewed; based on 2,182 US M&A deals 1990–2007 from Thomson ONE Banker): "The merger arbitrage spread has declined by more than 400 bps since 2002." Median first-day spread fell from 4.1–7.9% (pre-2001) to 1.7–2.6% (post-2001); aggregate hedge-fund alpha declined ~41 bps per period, "equivalent to an annual decline of 4.81 percentage points," and median monthly fund returns fell from 0.96% (1990–1995) to 0.51% (2002–2007). Broad strategy returns: ~4.4% annualized 1998–2022 (Carmignac, practitioner), ~200bp over risk-free. The decline was judged "likely permanent" due to more capital and lower trading costs.
- **Closed-end fund discounts.** The classic Lee, Shleifer & Thaler (1991) sentiment puzzle; discounts persist but activist arbitrage (Bradley, Brav, Goldstein & Jiang, "Activist arbitrage," *JFE* 2010) reduces discounts by ~half / >8–10 percentage points. Saba Capital is the dominant activist; capacity is small and increasingly crowded (ICI: a record number of CEFs targeted in 2023).
- **Index-inclusion effects.** Greenwood & Sammon, "The Disappearing Index Effect" (now published in *The Journal of Finance* 80(2):657–698, April 2025): "The abnormal return associated with a stock being added to the S&P 500 has fallen from an average of 7.4% in the 1990s to 0.3% over the past decade." The deletion effect fell from −16.1% (1990s) to −0.6% (2010s) — both statistically indistinguishable from zero recently, despite rising indexation. Driven by migrations, front-running, and liquidity provision.
- **Spinoffs.** Older studies (Cusatis, Miles & Woolridge; McConnell) show large positive excess returns (12–28% over 1–3 years); a 2000–2022 replication (CBS thesis, not peer-reviewed) claims the anomaly persists ("unrelenting"), but this is a student thesis and should be treated cautiously.
- **Post-bankruptcy equities.** Eberhart, Aggarwal & Altman (1999, *The Journal of Finance*): large positive abnormal returns in the ~200 days after relisting. But later work is mixed-to-negative; Verdad's practitioner analysis found median post-reorg equity returns of −15% (1yr) and −35% (2yr), with 22% suffering >80% losses within two years. High dispersion, not a clean anomaly.

Which survive: McLean & Pontiff (2016, *The Journal of Finance* 71(1):5–32) find, verbatim, "Portfolio returns are 26% lower out-of-sample and 58% lower post-publication," estimating "a 32% (58%–26%) lower return from publication-informed trading." Hou, Xue & Zhang (2020, "Replicating Anomalies," *Review of Financial Studies*) — most of ~450 anomalies fail to replicate with microcap-robust methods (NYSE breakpoints, value-weighting), largely because they were driven by microcaps and ignored costs.

### 4. Transaction cost reality in microcaps

- **Spreads.** Lesmond, Ogden & Trzcinka (1999, *Review of Financial Studies* 12(5):1113–1141), verbatim: "the model provides continuous estimates of average round-trip transaction costs from 1963 to 1990 that are 1.2% and 10.3% for large and small decile firms, respectively." Modern estimators (Corwin & Schultz 2012, *Journal of Finance*; Abdi & Ranaldo 2017, *RFS*; Goyenko, Holden & Trzcinka 2009, *JFE*) confirm microcaps remain the widest-spread segment.
- **Volume/thinness.** Median microcap ADV ≈ $200K–$325K/day; ~75% of microcaps trade <$1M/day (MicroCapClub/Portfolio123, practitioner).
- **Market impact.** Square-root law I = Y·σ·√(Q/V), Y≈0.5–1.0 (Torre/BARRA, Almgren et al., Tóth et al., Bouchaud). A $50K order in a $300K-ADV name is ~15–25% of ADV → predicted impact ~1–2%+ and outside the model's validated range (Frazzini, Israel & Moskowitz 2012 note their calibration only covers trades <5–13% of ADV). A $500K order is multiples of daily volume — effectively must be worked over days or is untradeable. (These impact figures are illustrative calculations applying the published formula, not empirical observations for specific names.)
- **Realized costs.** FIM (2012, "Trading Costs of Asset Pricing Anomalies," AQR working paper): ~12bp average market impact, ~21bp small-cap, average trade $695K at 1.2% of ADV — BUT these are a large patient arbitrageur's costs in large/mid-caps, explicitly "not those facing the average investor … but rather those of an arbitrageur." Patton & Weller (2020, *JFE*, "What You See Is Not What You Get") rebut the optimistic view; Novy-Marx & Velikov (2016, *RFS*, "A Taxonomy of Anomalies and Their Trading Costs") show many anomalies have low break-even capacity net of costs; Korajczyk & Sadka (2004) put long-only momentum break-even at just $2–5B.
- **Borrow costs.** For shorting (relevant to merger-arb hedges and CEF pair trades): sub-$100M-cap stocks averaged >30% borrow fees in 2023 (Daniel et al. 2024, cited via Acadian); "special" stocks (>1% fee) rose from <10% of names in 2001 to ~50% in 2023. Hard-to-borrow names run 25–100%+ annualized. This can consume the entire spread on any short leg.
- **Viability threshold:** Below ~$50K–$100K per position, the most liquid event names (larger merger targets, liquid CEFs) can clear costs. In true sub-$300M microcaps, positions above a few hundred thousand dollars are not viable for a price-taking small operator.

### 5. Data infrastructure

- **Institutional tier:** CRSP + Compustat (via WRDS) — survivorship-bias-free with delisting returns; Compustat Point-in-Time preserves as-filed (not restated) fundamentals. Priced for institutions/universities; effectively out of reach for an individual except via academic affiliation.
- **Retail-accessible tier:** Norgate Data (includes delisted tickers and historical index membership); QuantConnect/LEAN (point-in-time constituents); sec-api / EdgarTools / SEC EDGAR (free, structured filings back to ~1994). SEC EDGAR full-text search and the raw filings are free and are the natural primary source for an extraction-based strategy.
- **What is lost at cheaper tiers:** free fundamentals sources typically carry restated (not as-filed) numbers → restatement bias (1–2pp/yr for value strategies); many lack delisted securities → survivorship bias (CRSP shows ~1.6pp/yr difference 1926–2001); corporate-action history and point-in-time index membership are frequently missing or approximate. For event-driven strategies the terminal delisting return is often the single most informative datapoint and is precisely what cheap sources omit.

### 6. Regulatory boundary (US)

- **Trading your own capital:** No registration required. The Advisers Act, broker-dealer rules, and CPO/CTA rules are triggered by acting for others.
- **Investment Adviser:** Managing others' money for compensation triggers registration — state-level below $100M AUM, SEC generally at $100M+. Even below thresholds, advising others for a fee implicates state registration and fiduciary duty (NASAA).
- **Broker-Dealer:** Triggered by being "in the business" of buying/selling securities for others' accounts or as dealer; net capital rules (e.g., $250K for carrying firms). Not implicated by trading solely for one's own account "not as part of a regular business" (Exchange Act §3(a)(5) exclusion).
- **Publishing / selling signals:** The Investment Advisers Act §202(a)(11)(D) publisher's exclusion (upheld in *Lowe v. SEC*, 1985) protects bona fide publications that are (i) impersonal, (ii) disinterested/bona fide, and (iii) of general and regular circulation not timed to specific market events. Critically, the SEC has treated an "auto-trading" program that sends signals which are automatically traded as personalized investment advice requiring registration — so selling a tool that produces individualized trade signals likely forfeits the publisher's exclusion and triggers adviser registration.
- **CPO/CTA (CFTC/NFA):** Triggered only if trading commodity interests/futures for others — not implicated by cash-equity own-account trading.

### 7. Existing landscape

- **Academic/open-source:** FinRL and FinRL-DeepSeek (Benhenda, arXiv 2502.07393) integrate LLM news signals into RL trading agents — published with code; results are backtested and regime-sensitive (LLM signals only helped when combined with CVaR risk control; naively injecting them into plain PPO hurt performance). "When Agents Trade" (arXiv 2510.11695) live-trades LLM agents with mixed results and no consistent winner. BloombergGPT and FinBERT are domain models but not full strategies.
- **Commercial:** SEC-filing extraction vendors (LlamaIndex/LlamaExtract, sec-api, edgar.tools, Quantillium) target hedge-fund research workflows; they publish capability demos, not audited returns. A-Team Insight and others note the deterministic-vs-LLM architectural split — deterministic pipelines offer auditability, LLMs offer breadth — which is exactly the hybrid proposed here.
- **Published failure modes:** the recurring, well-documented failure is look-ahead/memorization contamination in the prediction layer (Benhenda; Sarkar & Vafa; Gao et al.), plus overreaction to news noise when LLM signals are injected without risk control (FinRL-DeepSeek).

## Recommendations

**Stage 0 — Constrain scope and stay legal (immediate).** Trade only your own capital. Do not accept others' money and do not sell individualized signals; if you ever publish, keep it impersonal, general-circulation, and not timed to specific trades to stay within the publisher's exclusion. This keeps you entirely outside adviser/BD/CTA registration.

**Stage 1 — Build and audit the extraction layer first (months 1–3).** Use free SEC EDGAR as the primary source and open tooling (EdgarTools/sec-api). Before any trading, measure extraction precision/recall on a hand-labeled sample of the actual documents you will parse (merger agreements, 8-Ks, bankruptcy dockets), because published F1 (~0.82) is from news text and ORG extraction is the weak point. **Benchmark to change decision:** if field-level precision on deal terms is below ~95% on a held-out, post-training-cutoff sample, the deterministic layer downstream will inherit unacceptable error — fix extraction before proceeding.

**Stage 2 — Pick the least-decayed, most-capacity-appropriate niche.** On the evidence, closed-end fund discount/activist situations and small merger-arb deals are the most defensible for a sub-$500K operator: the anomaly mechanism is structural (forced convergence at NAV / deal close) rather than purely statistical, and capacity is small enough that institutions are absent. Avoid index-inclusion effects (documented to ~zero) and treat post-bankruptcy equities as high-dispersion, not a clean edge.

**Stage 3 — Cost-first backtest, then paper-trade live (months 3–9).** Model spreads at smallest-decile levels (assume ~5–10% round-trip in true microcaps), impact via the square-root law, and real borrow costs on any short leg. **Threshold:** if net-of-cost expected return per event is not at least ~2–3× your modeled all-in transaction cost, do not trade it. Then paper-trade with realistic (not mid-quote) fills for at least 6 months before committing capital.

**Stage 4 — Size to liquidity, not to conviction.** Cap each position at a small fraction (e.g., ≤10–15%) of the name's median daily dollar volume. In practice this means ~$20K–$50K positions in typical microcaps and only larger sizes in liquid CEFs/larger merger targets.

**What would change the recommendation:** If you can obtain point-in-time survivorship-free data (academic affiliation/WRDS) AND demonstrate post-cutoff extraction precision >95% AND show a live paper-trading Sharpe meaningfully above 1 net of realistic costs over 12 months, scale cautiously. Absent any one of these, treat it as a research hobby, not a capital-allocation strategy.

## Caveats
- **Backtests here are systematically optimistic.** Survivorship bias, restatement bias, look-ahead contamination in any LLM step, and understated microcap transaction costs all push realized returns below backtested ones. Assume a large haircut.
- **Several key figures are practitioner or preprint, not peer-reviewed.** The microcap ADV figures (MicroCapClub), merger-arb aggregate returns (Carmignac), Benhenda's Look-Ahead-Bench, and Lopez-Lira & Tang are flagged accordingly in the body. The strongest peer-reviewed anchors are Lu & Huo (extraction), Glasserman & Lin and Sarkar & Vafa (look-ahead), Jetley & Ji and Greenwood & Sammon (decay), Lesmond et al. and Corwin & Schultz (costs), and McLean & Pontiff and Hou-Xue-Zhang (replication).
- **The square-root-law impact numbers are illustrative calculations** applying the published formula, not empirical observations for your specific names.
- **All performance figures cited are backtested or (for merger-arb indices) index-level historical; none represent live results of this specific architecture, which has no published live track record.**

## Three findings that most WEAKEN the case
1. **The target anomalies have measurably decayed toward zero** (S&P 500 addition effect 7.4%→0.3%; merger-arb spreads −400bp; McLean-Pontiff 58% lower returns post-publication), so the raw edge being extracted is a fraction of what historical studies suggest.
2. **Microcap transaction costs plausibly exceed the residual edge for a small operator** — ~10.3% historical smallest-decile round-trip cost and ~$200–325K median daily volume mean spread plus impact can consume the entire theoretical spread on anything but the smallest positions.
3. **Every LLM step imports contamination risk that is hard to fully audit** — even though extraction is safer than prediction, look-ahead/memorization has repeatedly turned double-digit backtested alpha negative out-of-sample, and there is no consensus validation protocol.

## Three findings that most STRENGTHEN the case
1. **The extraction-only design sidesteps the most damaging, best-documented failure mode.** The contamination literature indicts prediction, not fact-extraction; keeping the LLM as a text-to-data layer feeding a deterministic engine is the correct architectural response to that literature.
2. **The niches are capacity-constrained for structural reasons, so a small operator is not competing with institutional capital** — closed-end fund and small-merger situations are too small for large funds, and the convergence mechanisms (NAV, deal close) are structural rather than statistical.
3. **Extraction performance is genuinely usable and cheap to build** — F1 in the low-0.80s with prompting, free primary data (SEC EDGAR), and mature open tooling mean the pipeline can be built and audited by one technical person at near-zero data cost.

## The specific empirical tests that would resolve the largest uncertainties
1. **Post-cutoff extraction accuracy audit.** Hand-label deal terms/covenants/guidance changes in a sample of documents published AFTER the LLM's training cutoff; measure field-level precision/recall. This resolves both extraction quality and contamination in one test.
2. **Realistic-fill live paper trade.** Run the full pipeline forward for 6–12 months on live events with fills modeled at the bid/ask (not mid) plus square-root impact and actual borrow quotes. This resolves whether the net-of-cost edge is real at achievable size.
3. **Cost-sensitivity capacity curve.** Compute net return as a function of position size using empirical spreads and the square-root impact law for your actual universe, to locate the exact dollar threshold where the strategy goes cash-flow negative. This resolves the capacity question quantitatively.