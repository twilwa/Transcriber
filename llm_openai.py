import dataclasses
import logging
import urllib.error
import time
import re
import json

import openai
import tiktoken
import i18n

import main_types as t

default_model_name = "gpt-4-0613"

implied_tokens_per_request = 3


def _num_tokens_from_messages(messages, model: str):
    """
    Return the number of tokens used by a list of messages.
    code from: https://github.com/openai/openai-cookbook/blob/main/examples/How_to_count_tokens_with_tiktoken.ipynb
    """
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        logging.warning("Warning: model not found. Using cl100k_base encoding.")
        encoding = tiktoken.get_encoding("cl100k_base")
    if model in {
        "gpt-3.5-turbo-0613",
        "gpt-3.5-turbo-16k-0613",
        "gpt-4-0314",
        "gpt-4-32k-0314",
        "gpt-4-0613",
        "gpt-4-32k-0613",
    }:
        tokens_per_message = 3
        tokens_per_name = 1
    elif model == "gpt-3.5-turbo-0301":
        tokens_per_message = 4  # every message follows <|start|>{role/name}\n{content}<|end|>\n
        tokens_per_name = -1  # if there's a name, the role is omitted
    elif "gpt-3.5-turbo" in model:
        logging.warning(
            "Warning: gpt-3.5-turbo may update over time. Returning num tokens assuming gpt-3.5-turbo-0613.")
        return _num_tokens_from_messages(messages, model="gpt-3.5-turbo-0613")
    elif "gpt-4" in model:
        logging.warning("Warning: gpt-4 may update over time. Returning num tokens assuming gpt-4-0613.")
        return _num_tokens_from_messages(messages, model="gpt-4-0613")
    else:
        raise NotImplementedError(
            f"""num_tokens_from_messages() is not implemented for model {model}"""
        )
    num_tokens = 0
    for message in messages:
        num_tokens += tokens_per_message
        for key, value in message.items():
            num_tokens += len(encoding.encode(value))
            if key == "name":
                num_tokens += tokens_per_name
    num_tokens += 3  # every reply is primed with <|start|>assistant<|message|>
    return num_tokens


def _num_tokens_from_message(role: str, content: str, model: str):
    return _num_tokens_from_messages([{"role": role, "content": content}], model) - implied_tokens_per_request


def _check_token_limits(messages, model_name):
    if _num_tokens_from_messages(messages, model_name) > 3096:
        raise RuntimeError(
            "The number of tokens exceeds the limit of 3096; messages = %s" %
            json.dumps(messages, indent=2, ensure_ascii=False))


def _invoke(messages, model_name: str):
    try_count = 0
    r0 = None
    while True:
        try_count += 1
        try:
            r0 = openai.ChatCompletion.create(
                model=model_name,
                messages=messages
            )
            break
        except openai.error.AuthenticationError as ex:
            raise ex
        except (urllib.error.HTTPError, openai.OpenAIError) as ex:
            if try_count >= 3:
                raise ex
            time.sleep(10)
            continue

    finish_reason = r0["choices"][0]["finish_reason"]
    if finish_reason != "stop":
        raise RuntimeError("API finished with unexpected reason: " + finish_reason)

    return r0["choices"][0]["message"]["content"]


def _invoke_with_retry(messages, model_name: str, post_process=None):
    _check_token_limits(messages, model_name)
    r0 = ""
    for _ in range(3):
        r0 = _invoke(messages, model_name)
        if post_process is None:
            return r0
        processed, r1 = post_process(r0)
        if not processed:
            logging.info("llm._invoke_with_retry: retry")
            continue
        return r1
    else:
        raise RuntimeError(
            "Invalid response returned more than the specified number of times;"
            " messages = %s, last response = \"%s\"" % (json.dumps(messages, indent=2, ensure_ascii=False), r0))


@dataclasses.dataclass
class QualifyOptions:
    input_language: str = "ja"
    output_language: str = "ja"
    model_for_step1: str = default_model_name
    model_for_step2: str = default_model_name


_qualify_p0_system_ja = '''\
次の文章は会議中の会話を機械的に書き起こしたものです。
この文章を訂正し、会議の会話として意味のある内容のみ抽出してください。
そのために、書き起こし誤りと推測される箇所を修正し、フィラーや言い直しを除去してください。
入力される文章の各行は発言者の名前の後に ":" が続き、さらに発言内容が続きます。出力も同じ書式を維持してください。
人名が日本語表記ではない場合、人名は原表記を維持してください。'''

_qualify_p0_template_no_embeddings = '''\
The following %(source_language_descriptor)s text is a mechanical transcription of a conversation during a meeting.
Please correct this sentence and extract only what makes sense as a meeting conversation.
To do so, please correct what you assume to be transcription errors and remove fillers and rephrasing.
%(output_language_descriptor)s'''

_qualify_p0_template_with_embeddings = '''\
The following %(source_language_descriptor)s text is a mechanical transcription of a conversation during a meeting.
Please correct this sentence and extract only what makes sense as a meeting conversation.
To do so, please correct what you assume to be transcription errors and remove fillers and rephrasing.
Each line of input text is the speaker's name followed by ":" and then the content of the statement.
Output should maintain the same format.
%(output_language_descriptor)s'''

_qualify_p1_system_ja = '''\
次の文章は会議中の会話を書き起こした議事録です。
この議事録から、要約とアクションアイテムを抽出してください。
要約には固有名詞もしくは議論内容のみ含めることとし、一般知識や既知の事柄で情報を補わないでください。
アクションアイテムには議事の中で参加者から明示的な言及があったもののみ含めることとし、推測は含めないでください。
要約は "point:" に続けて1件のみ出力し、アクションアイテムは "action item:" を行頭に付加してください。
例えば以下のような形式です。
point: 要点の例。文章にしてください。
action item: アクションアイテムの例
action item: アクションアイテムは複数になることもあります。
人名が日本語表記ではない場合、人名は原表記を維持してください。
出力すべき情報が特にない場合や要約に必要な情報が足りない場合は、"none" とだけ出力してください。'''

_qualify_p1_template = '''\
The following text is the transcribed minutes of a conversation during a meeting.
From this transcript, please extract a summary and action items.
The summary should include only proper nouns or the content of the discussion,
and should not be supplemented with general knowledge or known facts.
Action items should only include items explicitly mentioned by participants in the agenda, 
and should not include speculation.
Only one summary should be printed following the "point:" and the action item should be prefixed with "action item:".
For example, the format is as follows:
%(output_example_descriptor)s
If there is no particular information to be output, or if there is not enough information for the summary,
just output "none".'''

_qualify_p2_system_ja = '''\
次の文章は会議中の会話を機械的に書き起こしたものです。
この議事録から、要約とアクションアイテムを抽出してください。
入力される文章に含まれる発音が近い単語への書き起こし誤りは訂正し、フィラーや言い直しは無視してください。
要約には固有名詞もしくは議論内容のみ含めることとし、一般知識や既知の事柄で情報を補わないでください。
アクションアイテムには議事の中で参加者から明示的な言及があったもののみ含めることとし、推測は含めないでください。
要約は "point:" に続けて1件のみ出力し、アクションアイテムは "action item:" を行頭に付加してください。
例えば以下のような形式です。
point: 要点の例。文章にしてください。
action item: アクションアイテムの例
action item: アクションアイテムは複数になることもあります。
人名が日本語表記ではない場合、人名は原表記を維持してください。
出力すべき情報が特にない場合や要約に必要な情報が足りない場合は、"none" とだけ出力してください。'''

_qualify_p2_template = '''\
The following %(source_language_descriptor)s text is a mechanical transcription of a conversation during a meeting.
From this transcript, please extract a summary and action items.
Transcription errors to closely pronounced words in the input text should be corrected, 
and fillers and rephrasing should be ignored.
The summary should include only proper nouns or the content of the discussion,
and should not be supplemented with general knowledge or known facts.
Action items should only include items explicitly mentioned by participants in the agenda, 
and should not include speculation.
Only one summary should be printed following the "point:" and the action item should be prefixed with "action item:".
For example, the format is as follows:
%(output_example_descriptor)s
If there is no particular information to be output, or if there is not enough information for the summary,
just output "none".'''

_source_language_descriptor = {
    "en": "English",
    "ja": "Japanese"
}

_output_language_descriptor_for_p0 = {
    "en": {
        "default":
            "Output should be in English. Names of non-English spelling should not be converted to English, "
            "but should be retained in their original spelling.",
        "translate":
            "Please translate the output into English. "
            "Names of non-English spelling should not be converted to English, "
            "but should be retained in their original spelling.",
    },
    "ja": {
        "default":
            "出力は日本語にしてください。ただし、日本語表記ではない人名は日本語に変換せず、原表記を維持してください。",
        "translate":
            "日本語に翻訳して出力してください。ただし、日本語表記ではない人名は日本語に変換せず、原表記を維持してください。",
    }
}

_output_example_descriptor_for_p1 = {
    "en":
        "point: An example of a main point. Please use sentence form, not a list of words.\n"
        "action item: An example of an action item.\n"
        "action item: There can be more than one action item.\n"
        "The words after \":\" should be written in English. "
        "However, names of non-English spelling should not be converted to English, "
        "but should be retained in their original spelling.",
    "ja":
        "point: 要点の例。文章にしてください。\n"
        "action item: アクションアイテムの例\n"
        "action item: アクションアイテムは複数になることもあります。\n"
        "\":\" 以降は日本語にしてください。ただし、日本語表記ではない人名は日本語に変換せず、原表記を維持してください。",
}


def _qualify_p0_system(opt: QualifyOptions, with_embeddings: bool):
    return (_qualify_p0_template_with_embeddings if with_embeddings else _qualify_p0_template_no_embeddings) % {
        "source_language_descriptor": _source_language_descriptor[opt.input_language],
        "output_language_descriptor": _output_language_descriptor_for_p0[opt.output_language][
            "default" if opt.input_language == opt.output_language else "translate"]
    }


def _qualify_p1_system(opt: QualifyOptions):
    return _qualify_p1_template % {
        "output_example_descriptor": _output_example_descriptor_for_p1[opt.output_language]
    }


def _qualify_p2_system(opt: QualifyOptions):
    return _qualify_p2_template % {
        "source_language_descriptor": _source_language_descriptor[opt.input_language],
        "output_example_descriptor": _output_example_descriptor_for_p1[opt.output_language]
    }


def _get_name(s: t.Sentence):
    return s.person_name if s.person_id != -1 else t.unknown_person_name


def _correct_sentences_no_embeddings(sentences: list[t.Sentence], model_name: str, opt: QualifyOptions) -> str:
    if len(sentences) == 0:
        return ""

    text = " ".join([s.text for s in sentences])
    messages = [
        {"role": "system", "content": _qualify_p0_system(opt, with_embeddings=False)},
        {"role": "user", "content": text}
    ]

    return _invoke_with_retry(messages, model_name)


def _correct_sentences_with_embeddings(
        sentences: list[t.Sentence], model_name: str, opt: QualifyOptions) -> list[t.Sentence]:

    if len(sentences) == 0:
        return []

    text = "\n".join([_get_name(s) + ": " + s.text.replace("\n", "\n  ") for s in sentences])
    messages = [
        {"role": "system", "content": _qualify_p0_system(opt, with_embeddings=True)},
        {"role": "user", "content": text}
    ]

    name_to_id = {_get_name(s): s.person_id for s in sentences if s.person_id != -1}

    def _post_process(r0_):
        r1_ = re.findall(r"([^:]+): (.+)\n*", r0_)
        return r1_ is not None and len(r1_) != 0, r1_

    r1 = _invoke_with_retry(messages, model_name, _post_process)

    return [t.Sentence(
        sentences[0].tm0, sentences[-1].tm1, e[1], person_name=e[0],
        person_id=name_to_id[e[0]] if e[0] in name_to_id else -1) for e in r1]


def _summarize_sub(sentences: list[t.Sentence] | str, model_name: str, prompt: str) -> tuple[str, list[str]]:
    if len(sentences) == 0:
        return "", []

    text = "\n".join([_get_name(s) + ": " + s.text.replace("\n", "\n  ") for s in sentences]) \
        if isinstance(sentences, list) else sentences
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": text}
    ]

    def _post_process(r0_):
        r1_ = re.findall(r"point: (.+)\n*", r0_)
        if r1_ is None or len(r1_) != 1:
            return False, None
        r1_[0] = "(なし)" if r1_[0] == "none" else r1_[0]
        r2_ = re.findall(r"action item: (.+)\n*", r0_)
        r2_ = [] if r2_ is None else list(filter(lambda e_: e_ != "none", r2_))
        return True, (r1_, r2_)

    r1, r2 = _invoke_with_retry(messages, model_name, _post_process)
    return r1[0], r2


def _summarize(sentences: list[t.Sentence] | str, model_name: str, opt: QualifyOptions) -> tuple[str, list[str]]:
    return _summarize_sub(sentences, model_name, _qualify_p1_system(opt))


def _qualify(sentences: list[t.Sentence], model_name: str, opt: QualifyOptions) -> tuple[str, list[str]]:
    return _summarize_sub(sentences, model_name, _qualify_p2_system(opt))


def qualify(
        sentences: list[t.Sentence],
        opt: QualifyOptions | None = None) -> t.QualifiedResult:

    if opt is None:
        opt = QualifyOptions()

    has_embedding = (len([None for s in sentences if s.embedding is not None]) != 0)

    try:
        corrected = _correct_sentences_with_embeddings(sentences, opt.model_for_step1, opt) \
            if has_embedding else _correct_sentences_no_embeddings(sentences, opt.model_for_step1, opt)
        summaries, action_items = _summarize(corrected, opt.model_for_step2, opt)
    except openai.error.AuthenticationError:
        return t.QualifiedResult(
            corrected_sentences=sentences,
            summaries=i18n.t('app.qualify_llm_authentication_error'),
            action_items=[]
        )

    return t.QualifiedResult(
        corrected_sentences=sentences,  # keep original
        summaries=summaries,
        action_items=action_items
    )