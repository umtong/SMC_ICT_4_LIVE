# Candidate 4t v7 — immutable filled-position lifecycle

The alpha policy remains v6. Version 7 corrects an account-state error discovered before continuous evaluation.

Earlier research routers used one `terminal_ns` for both an unfilled instruction and a filled position. When a labelled action remained unresolved at that timestamp, the router could release the account slot even though the declared TP or SL had not traded. That contradicts the project rule and can admit overlapping positions into an apparently single-slot account.

Version 7 separates the two lifecycles:

- before fill, the causal order terminal may cancel an obsolete pending instruction;
- after fill, the account remains committed until the predeclared stop or target resolves;
- a resolved fill releases the slot at `resolution_time_ns`;
- an unresolved filled position remains open through the end of the observed data and blocks every later candidate;
- an unfilled pending order may still be replaced by a better independent causal episode;
- the summary reports open filled positions and pending orders rather than silently dropping them.

This is an implementation-validity correction, not an additional risk rule or an alpha claim. The trajectory ownership, competing hypotheses, exact route and global commitment value are unchanged from v6.
