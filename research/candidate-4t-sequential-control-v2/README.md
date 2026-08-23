# Candidate 4t v2 — state ownership and global commitment value

Version 2 keeps the immutable causal actions and the sequential decision policy from v1, but removes two remaining structural errors.

First, directional ownership is no longer trained on duplicated entry rows. Every market state owns one action-independent soft control label, then fill and resolution are priced separately for each actual first-return instruction. This prevents OB/FVG/entry geometry from silently voting on direction.

Second, `WAIT / ENTER / ABANDON`, pending replacement, and one-account routing already existed elsewhere in the repository. The unresolved gap was the value of the **global slot commitment**. Once a limit fills, the declared TP/SL cannot be abandoned merely because another symbol later becomes attractive. Therefore a valid entry must pay not only more than cash and the same episode's continuation value, but also more than the expected independent BTC/ETH/SOL/XRP opportunities that the filled position would block during its likely lifetime.

The new global reservation model is trained only from separated development periods. For each development state it observes the best later independent causal opportunity that arose before the immutable order's terminal time, then predicts that reservation value from information available now. At decision time the policy compares:

`enter now` versus `max(wait in this episode, probability of fill × global slot reservation)`.

There is still no performance score gate or arbitrary confidence threshold. Gross route RR below 1R remains absent at generation. A pending order may be replaced by a better independent causal episode; a filled position remains immutable until its predeclared TP or SL.
