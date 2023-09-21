# Transcriber

これは音声の書き起こしと議事録生成を自動で実行するアプリケーションです。
書き起こした内容はリアルタイムに出力され、ある程度の文章ごとに要約を自動で生成します。
また実験的機能として、話者を自動で判別させることもできます。

## System overview

- 人の声を検出するVADには [Silero VAD](https://github.com/snakers4/silero-vad) を使用しています。
- 書き起こしには [Faster Whisper](https://github.com/guillaumekln/faster-whisper) を使用しています。
  リアルタイムに処理するためには(バックオーダーを抱えた状態にしないためには)GPUの使用をおすすめしますが、
  低精度モデルであればCPU実行させることも可能です。<br/>
  多少セットアップが面倒なものの、ローカルネットワーク上のGPUマシンに通信経由で処理させることもできます。
- 話者識別のための特徴量ベクトル計算には [SpeechBrain](https://github.com/speechbrain/speechbrain) か、
  [Pyannote.audio](https://github.com/pyannote/pyannote-audio/) を選べます。<br/>
  話者識別のクラスタリングアルゴリズムはオリジナルですが、バックエンドとしてDBSCANを使用しています。
- 要約生成には [OpenAI API](https://platform.openai.com/docs/introduction) を使用します。
  このためにOpenAI API Keyが必要になります。
- UIは [Gradio](https://www.gradio.app/) で書かれており、ブラウザでローカルサーバにアクセスする形です。

## システム要件

- Python 3.11以降
- GPU VRAM 8GB以上 (optional)
- OpenAI API Key。なくても動作しますが、その場合は書き起こしまでしか動作しません。
- マイク (必須) とスピーカ (optional)。アプリケーション本体を動作させているマシンのマイクとスピーカを使用します。
  ブラウザ経由ではないことに注意してください。

## 導入

### 仮想環境のセットアップ (optional)

導入にあたりいくつかのパッケージをインストールするため、必要に応じてvenv等を設定してください。

dockerコンテナや仮想マシンを立ち上げる場合はローカルサーバが待ち受けるポート7860を見える状態にしてください。
また、マイクが見えるようにホスト側の `/dev/snd` をマウントする必要があるかもしれません。<br/>
(依存パッケージのインストールが終わった後、`test_sound_device.py`を実行してpassすればOKです)

### 依存パッケージのインストール

リポジトリ内のトップディレクトリに移動して以下のコマンドを実行してください。

```commandline
pip3 install -r requirements.txt
```

ここまで終わったら、念のため `test_sound_device.py` を実行してマイク入力をテストしてください。
`pass`と表示されたら成功です。

```commandline
python3 test_sound_device.py
```

環境によっては以下のような問題が起きることがあります。

- 録音デバイスが見つからない。例えばdockerコンテナでは `/dev/snd` をマウントしておく必要があります。
- ドライバ周りのエラー。発生したエラーに従って適宜対応してください。
  [例えばこれ。](https://stackoverflow.com/questions/49333582/portaudio-library-not-found-by-sounddevice)
- 録音自体はできるが、All 0になる。Macではterminalにマイクの許可が出ていないとこの現象が起きるようです。

### 追加機能・モデルのインストール (optional)

使用する機能に応じて追加でインストールしてください。

#### Pyannote/embedding

話者識別のための特徴量ベクトル計算にPyannoteを使う場合、手動でモデルをダウンロードしてください。

モデルは [HuggingFace pyannote/embedding](https://huggingface.co/pyannote/embedding) からダウンロードできます。
いわゆる gating model となっていて、ダウンロードするためにはメールアドレス等をフォームから登録する必要があります。
ダウンロードしたら`resources/pynannote_embedding_pytorch_model.bin`として配置してください。<br/>
(違うモデルに対するものですが
[こちら](https://github.com/pyannote/pyannote-audio/blob/develop/tutorials/applying_a_model.ipynb)
の最下部にダウンロード手順の詳細が記載されています)

#### Blackhole (Macのみ)

[Blackhole](https://existential.audio/blackhole/) は仮想オーディオデバイスをMacに追加します。
この仮想オーディオデバイスはスピーカに出力された音をマイク入力としてループバックする機能を持っており、
たとえばTeamsなどの音声出力をそのままTranscriberに渡すことができるようになります。

スピーカに出力しつつループバックさせたい場合は、 Audio MIDI設定から複数出力装置をセットアップしてください。

### アプリケーションの起動

terminalから`app.py`を起動してください。

```commandline
python3 app.py
```

最初はモデルのダウンロードが走るため時間がかかります。
`Running on local URL:  http://0.0.0.0:7860` という表示が出たら準備完了です。
ブラウザで `http://127.0.0.1:7860/` を開いてください。

### 初回セットアップ

デフォルト設定のままでも一応動きますが、「設定」タブから以下の設定を変更することをおすすめします。

1. 入力デバイスを指定する<br/>
   複数の入力を同時に使用できます。例えばマイクとループバック(上述のBlackholeなど)を同時に指定することで、
   リモート会議中の相手音声と自分の音声を同時に書き起こせます。
2. 認識処理を実行するデバイスの設定。"gpu" に設定すると高精度モデルを使うようになります。
3. 音声保存の設定。有効にすると、指定された日数分の音声データを保存します。書き起こしミスをあとから確認する際に便利です。
4. 話者特徴量ベクトルの計算アルゴリズムの設定。 `pyannote` の方が精度が高いようです。
   なお、上にある導入手順をあらかじめ実行しておく必要があります。
5. OpenAI API Keyの設定。

ひととおり設定が終わったら「設定を適用」を押して設定を書き込んでください。

再起動が必要と書いてある設定を変更しているので、再起動が必要になります。
terminalでCTRL-Cまたはkillコマンドにて終了させたあと、再度`app.py`を起動してください。


おつかれさまでした。これでセットアップは完了です!

## 使い方

ブラウザからアクセスしてください。
アドレスは通常 `http://127.0.0.1:7860/` ですが、仮想マシン経由の場合は異なるアドレスやポートになる可能性があります。

別のマシンからUIを開くこともできます。ただし音声の録音(と再生)はあくまで`app.py`を起動したマシンで実行することに注意してください。

音声の書き起こしはUIをブラウザで開いている間のみ実行されます。
UIを開いているブラウザがなくなるとアプリケーション本体はスリープ状態に入ります。

### 記録中タブ

現在書き起こしている内容がリアルタイムに表示されます。
当日分の履歴を確認することもできますが、更新が入ると下端まで自動でスクロールするため、
じっくり内容を確認したい場合は履歴タブから参照するとよいでしょう。

### 履歴タブ

書き起こした内容が日ごとにわけられて保存されています。ドロップダウンリストから履歴を参照したい日を選んでください。

当日分もここから参照できますが、この画面ではリアルタイム更新されません。

### 話者識別タブ

設定タブで話者特徴量ベクトルの計算アルゴリズムを有効にした場合、ここでデータベースの内容を確認できます。

音声がある程度の数集まると、クラスタリングが実行され人物が識別されます。
識別直後は適当な名前が付与されているので、人物を選択して名前を設定してください
(リストに出てこない場合は「最新の情報に更新」を押してください)。
変更した名前は履歴も含め可能な限り反映されますが、GPTで生成した要約には反映されません。

「クラスタリング結果を可視化する」にチェックを入れて更新すると(数十秒かかることがあります)、
話者特徴量ベクトルをt-SNEで圧縮してグラフ表示します。

### 設定タブ

アプリケーションの各種設定を変更します。
一部のオプションはアプリケーションの再起動が必要になります。

## ローカルネットワーク上のGPUマシンを使う

書き起こしや話者特徴量ベクトルの計算といった重いモデルの実行を
ローカルネットワーク上のGPUマシンに通信経由で処理させることもできます。

GPUマシン上で同様の環境構築をしたあと、`server_main.py`を常駐実行してください。例えば以下です。

```commandline
nohup python3 server_main.py &
```

通信には [gRPC](https://grpc.io/) が使用されています。
dockerコンテナや仮想マシンを経由していない場合はポート7860で待ち受けています。

GPUマシン側の用意ができたら、アプリケーションの設定タブにある「認識処理を実行するデバイス」に、
`アドレス:ポート番号`の形式で接続設定をしてください。


なお、通信量とのトレードオフからVADだけは必ずローカル実行になっています。
この節の設定をした場合でも、ある程度はアプリケーション本体でCPUを消費します。

## TODOs

- 言語の自動検出を実装する
- ローカルで動かせるLLMを試す
- 履歴のエクスポートに対応する
- 履歴の再解析(話者識別以降の再実行)に対応する
- 話者識別データベースの容量管理を実装する
- 各種パラメータのチューニング ※みなさまのフィードバックをお待ちしています。

## License

Apache License Version 2.0