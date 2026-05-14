def build_topic_lesson_prompt() -> str:
    return """
Bạn là một chương trình trích xuất cấu trúc từ SGK PDF.

MỤC TIÊU:
Đọc trang MỤC LỤC và trả về ĐÚNG 4 trường sau.
Python sẽ tự tính end từ start_printed — BẠN KHÔNG CẦN VÀ KHÔNG ĐƯỢC tự tính end.

TRƯỜNG CẦN TRẢ VỀ:
1. offset        : số nguyên = (số trang PDF thực) - (số in trên chân trang) cho bất kỳ trang nội dung chính nào.
2. printed_end_of_main : số trang IN của MỤC ĐẦU TIÊN không thuộc nội dung chính.
3. list_topic    : các CHỦ ĐỀ — mỗi mục CHỈ cần start_printed (số trang IN trong mục lục), heading, title.
4. list_lesson   : các BÀI — mỗi mục CHỈ cần start_printed (số trang IN trong mục lục), heading, title.

QUY TẮC NHẬN DIỆN:
1) LESSON (BÀI): CHỈ các dòng bắt đầu bằng đúng mẫu "Bài <SỐ>."
2) TOPIC (CHỦ ĐỀ): CHỈ các dòng bắt đầu bằng đúng mẫu "Chủ đề <SỐ>."
3) Nếu không chắc mục nào thì bỏ mục đó.

CÁCH XÁC ĐỊNH printed_end_of_main:
- Tìm dòng trong Mục lục ngay sau "Bài cuối cùng" mà KHÔNG phải "Bài <SỐ>." và có số trang.
- Trả về SỐ TRANG của dòng đó TRỰC TIẾP — KHÔNG trừ 1. Python sẽ trừ.
- Nếu không có dòng như vậy, trả về số trang in của trang nội dung cuối cùng + 1.

YÊU CẦU OUTPUT:
- Chỉ JSON thuần, KHÔNG giải thích, KHÔNG markdown.
- start_printed là số trang IN, KHÔNG phải số trang PDF.

FORMAT:
{
  "offset": 3,
  "printed_end_of_main": 158,
  "list_topic": [
    {"topic_01": {"start_printed": 3, "heading": "Chủ đề 1.", "title": "..."}}
  ],
  "list_lesson": [
    {"lesson_01": {"start_printed": 3, "heading": "Bài 1.", "title": "..."}}
  ]
}
"""


def build_topic_verify_prompt(full_topic_label: str) -> str:
    return f"""Bạn đang xem đúng 1 trang PDF (1 trang duy nhất).

NHIỆM VỤ: Xác định trang này CÓ PHẢI là trang BẮT ĐẦU THẬT SỰ của chủ đề sau không:
  "{full_topic_label}"

ĐỊNH NGHĨA "TRANG BẮT ĐẦU THẬT SỰ":
- Trang ĐẦU TIÊN nơi NỘI DUNG của chủ đề này thực sự bắt đầu.
- Nhãn chủ đề "{full_topic_label}" phải XUẤT HIỆN TRỰC TIẾP trên trang này như tiêu đề chương/chủ đề chính.
- KHÔNG PHẢI trang bắt đầu nếu đây là trang Mục lục, trang chỉ tham chiếu chủ đề, bìa, tóm tắt hoặc giới thiệu chung.

Trả về JSON thuần (không markdown, không giải thích):
{{
  "match": true,
  "is_toc_page": false,
  "full_label_exact": true,
  "confidence": 0.95
}}
"""


def build_chunk_prompt_start_head(total_pages: int) -> str:
    return f"""
Bạn đang đọc 1 file PDF chỉ chứa DUY NHẤT 1 BÀI (LESSON) (PDF scan).

MỤC TIÊU:
Trả về list_chunk là các MỤC CHÍNH CẤP CAO NHẤT của bài — và CHỈ những mục đó.

ĐỊNH NGHĨA MỤC CHÍNH HỢP LỆ:
1. Heading: "<SỐ>." đứng ĐẦU DÒNG riêng (ví dụ "1.", "2.", "3.").
2. Title: phần chữ ngay sau "<SỐ>." phải IN HOA TOÀN BỘ và là tên một chủ đề nội dung chính.

TUYỆT ĐỐI CẤM đưa vào list_chunk:
- a), b), c), d) hoặc mục con chữ thường
- danh sách bullet / gạch đầu dòng
- CÂU HỎI, BÀI TẬP, LUYỆN TẬP, VẬN DỤNG, ÔN TẬP
- NHIỆM VỤ, HOẠT ĐỘNG, KHỞI ĐỘNG, HƯỚNG DẪN
- BƯỚC, VÍ DỤ, THỰC HÀNH
- bất kỳ heading nào tự suy ra hoặc tự đặt tên

OUTPUT MỖI CHUNK:
- start: SỐ TRANG PDF (1-based) nơi tiêu đề mục chính xuất hiện lần đầu.
- content_head: true/false
- heading: CHỈ CHỨA SỐ MỤC dạng "1." / "2." / "3.".
- title: CHỈ PHẦN CHỮ SAU "<số>.", GIỮ NGUYÊN IN HOA.

content_head:
- true nếu trên CÙNG trang start, phía TRÊN tiêu đề còn có nội dung thuộc mục trước.
- false nếu phía trên chỉ có header/footer/số trang hoặc tiêu đề nằm ngay đầu trang nội dung.

RÀNG BUỘC:
- heading phải tăng dần theo thứ tự xuất hiện.
- 1 <= start <= {total_pages}.
- Nếu bài KHÔNG có mục chính hợp lệ => trả list_chunk rỗng [].

YÊU CẦU OUTPUT:
- Chỉ JSON thuần, KHÔNG giải thích, KHÔNG markdown.

FORMAT:
{{
  "list_chunk": [
    {{"chunk_01": {{"start": 1, "content_head": false, "heading": "1.", "title": "..."}}}},
    {{"chunk_02": {{"start": 3, "content_head": true, "heading": "2.", "title": "..."}}}}
  ]
}}
"""
