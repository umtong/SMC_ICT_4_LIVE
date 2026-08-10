# Public ZaratustraV5 source tournament

Every row is the project's four-symbol, one-slot, after-cost account with one-minute trailing detail. Every case JSON contains the complete compact trade ledger.

## development

| variant | trades | W/L | PF | geo/day | return | MDD | expectancy | signals | trail exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| source_level_both | 85 | 57/28 | 0.597807861428168 | -0.014664064787761388 | -0.18683157794460004 | 0.20684992724118145 | -219.80185640541177 | 235 | 54 |
| source_level_long | 54 | 37/17 | 0.6132102553054581 | -0.010491196752773768 | -0.1372692610520999 | 0.15080576311107707 | -254.20233528166665 | 154 | 37 |
| source_level_short | 51 | 33/18 | 0.44397062690492817 | -0.013497759332840586 | -0.17325216903099994 | 0.18873298663907367 | -339.710135354902 | 122 | 30 |
| source_edge_both | 73 | 48/25 | 0.5881625761009843 | -0.012283598840123977 | -0.15889209461529996 | 0.1780116200108791 | -217.66040358260273 | 118 | 46 |
| structural_level_both | 161 | 74/87 | 0.5342449292236086 | -0.06965030629510771 | -0.6360455231768 | 0.6716928075803245 | -395.0593311657143 | 361 | 72 |
| structural_level_short | 86 | 32/54 | 0.41153698648049947 | -0.05814222864166996 | -0.5676907207685 | 0.586747169111312 | -660.1054892656977 | 166 | 31 |

## reserved

| variant | trades | W/L | PF | geo/day | return | MDD | expectancy | signals | trail exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| source_level_long | 31 | 18/13 | 0.9254855861090521 | -0.000922101490507643 | -0.006436882155199997 | 0.04143189305058137 | -20.764135984516134 | 74 | 18 |
| source_level_both | 33 | 21/12 | 1.0629575700399136 | 0.0007451716941981879 | 0.00522787725039997 | 0.05700489212888893 | 15.842052273939393 | 82 | 21 |

## continuous_30d

| variant | trades | W/L | PF | geo/day | return | MDD | expectancy | signals | trail exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| source_level_both | 214 | 144/70 | 0.6810251307578566 | -0.013788726002652574 | -0.3406759958962 | 0.4027721849750645 | -159.19439060570093 | 580 | 145 |

## Allocation

- development survivors: ['source_level_long', 'source_level_both']
- positive reserved survivors: ['source_level_both']
- continuous winner: source_level_both
- strict project pass: False

Development allocation preserves quality and opportunity density rather than treating one threshold as a truth gate.
