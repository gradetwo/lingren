# VocalRender 音符 token 词表核验（Phase 1，源码实证）

来源：`vr/src/vocalrender/model/svs_utils.py` 的 `get_svs_token_maps()`，
与 `preprocessing/text_tensor.py` 的用法一致（`<NOTE_4>` 在词表里
index 6，即缺省值）。

| token | 拍数 | token | 拍数 |
|---|---|---|---|
| `<NOTE_1>` | 4.0 | `<NOTE_DOT_1>` | 6.0 |
| `<NOTE_2>` | 2.0 | `<NOTE_DOT_2>` | 3.0 |
| `<NOTE_4>` | 1.0 | `<NOTE_DOT_4>` | 1.5 |
| `<NOTE_8>` | 0.5 | `<NOTE_DOT_8>` | 0.75 |
| `<NOTE_16>` | 0.25 | `<NOTE_DOT_16>` | 0.375 |
| `<NOTE_32>` | 0.125 | `<NOTE_DOT_32>` | 0.1875 |

纠正：服务转换器初版误写成 `<NOTE_QUARTER>` / `<NOTE_HALF>` 等
英文名（不存在于词表，`note_tokens_list.index()` 会回退到缺省
`<NOTE_4>`，即所有音符悄悄变成一拍——静默错误）。已修正为上表，
转换时按拍数最近量化；超出 0.125–6.0 拍范围的音符仍会量化到最近
档，调用方应在请求层拒绝超长音符（Phase 2 接线时加校验）。
