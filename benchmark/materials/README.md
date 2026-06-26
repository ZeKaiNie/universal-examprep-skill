# materials/ —— 放你的真实课件 / 作业

学生的资料是**复合、多样**的，所以**一门课一个文件夹，课内再按类型分子文件夹**——不要一股脑全堆在一起。

```
materials/
  ds/                        # 课程：数据结构
    slides/                  #   讲义 / PPT / PDF
      ch3_stack.pdf
      ch5_search.pdf
    homework/                #   作业（题目 + 你的/标准答案）
      hw2.pdf
    exams/                   #   历年卷 / 样卷 / example_exam
      2025_final_sample.pdf
    notes/                   #   课堂笔记 / 老师划重点截图
      key_points.png
  co/                        # 课程：计算机组成原理
    slides/ ...
```

约定的子类型：`slides`（讲义）、`homework`（作业）、`exams`（样卷/真题）、`notes`（笔记/划重点）。
够用就行，缺哪类不放即可；计算题尽量从 `homework` / `exams` 里出，便于做「计算题准确率」。

## 两种放法，二选一

**A. 你自己分好类**：按上面的结构把文件拖进对应子文件夹。最省心、最可控。

**B. 全丢一个收件箱，让 AI 自动分类**（适合懒得手动整理）：
1. 把这门课所有文件先全丢进 `materials/<课程>/_inbox/`；
2. 在 Claude Code 里发一句：
   > 「请把 `benchmark/materials/ds/_inbox/` 里的文件按类型分类，新建 `slides/ homework/ exams/ notes/` 子文件夹并把文件移过去；无法判断的留在 `_inbox/` 并列个清单告诉我。」
3. AI 会自动建目录、归类，拿不准的会留给你确认。

## 还要做一步：生成 `_combined.txt`（基线臂要用）

基线臂是「没装 skill 的普通 AI」，得把**同样的材料**喂给它才公平。把该课程下所有资料的**纯文本**汇总成一个文件：
> 在 Claude Code 里说：「把 `benchmark/materials/ds/` 下所有 PDF/PPT/图片**递归**转成纯文本，合并写到 `benchmark/materials/_combined.txt`。」

（一次测一门课；换课程时重新生成 `_combined.txt` 即可。）

## ⚠️ 隐私 & 版权

跑测试时这些材料会被发送给 Claude（云端）。**别放隐私信息**（成绩单、身份证…）。讲义版权属老师/学校，
本目录已在 `.gitignore` 里被忽略，**不会被提交到公开仓库**——放心用，但也别自己手动 push 上去。
