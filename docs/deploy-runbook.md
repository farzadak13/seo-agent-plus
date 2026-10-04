# راهنمای انتشار و اتصال tennisino.com

هر بلوک را به ترتیب اجرا کن. دستورهای «روی ویندوز» در PowerShell داخل
`D:\Projects\SEOAGENT`، و دستورهای «روی سرور» بعد از `ssh seo-deploy`.

## ۱. ساختن و فرستادن نسخهٔ جدید (روی ویندوز)

    git archive --format=tar.gz -o release.tgz main
    scp release.tgz seo-deploy:/tmp/

## ۲. نصب نسخه (روی سرور)

    mkdir -p ~/release && tar -xzf /tmp/release.tgz -C ~/release deploy
    cd ~/release/deploy
    sudo bash app-release.sh /tmp/release.tgz

`app-release.sh` اگر `/readyz` جواب ندهد خودش به نسخهٔ قبل برمی‌گردد.
از اینجا به بعد اسکریپت‌ها را از نسخهٔ نصب‌شده اجرا کن:

    cd /opt/seoagent/current/deploy

## ۳. ایمنی، یک بار (روی سرور)

    sudo bash backup-setup.sh
    sudo seoagent-restore-test
    sudo bash watchdog-setup.sh
    sudo bash secret-key.sh

بعد از `secret-key.sh` یک نسخه از `/etc/seoagent/env` را جایی امن و جدا از
backup ها نگه دار. بدون آن کلید، رمزهای ذخیره‌شدهٔ سایت‌ها قابل خواندن نیستند.

## ۴. اتصال tennisino.com (روی سرور)

اول ببین Search Console این property را با چه رشته‌ای می‌شناسد:

    sudo bash smoke.sh

در خروجی آخرش فهرست property ها هست. همان رشته را **دقیقاً** بده (با اسلش
آخر، یا به شکل `sc-domain:tennisino.com`):

    sudo bash connect-site.sh https://tennisino.com/ <property-دقیق> Tennisino

نام کاربری وردپرس و application password را خود اسکریپت می‌پرسد؛ رمز روی
صفحه دیده نمی‌شود و جایی چاپ یا ذخیرهٔ خام نمی‌شود. در پایان باید `ok` و
نسخهٔ افزونه را ببینی. `site_id` را یادداشت کن.

## ۵. اتصال آروان (روی سرور)

با یک ویرایشگر (`sudo nano /etc/seoagent/env`) این‌ها را اضافه کن:

    SEO_AGENT_LLM_MODE=arvan
    SEO_AGENT_ARVAN_ENDPOINT=<آدرس endpoint از پنل آروان>
    SEO_AGENT_ARVAN_MODEL=<نام مدل>
    SEO_AGENT_ARVAN_API_KEY_REF=ARVAN_AI_KEY
    ARVAN_AI_KEY=<کلید>

بعد:

    sudo bash check-llm.sh

هر دو مرحله باید `ok` بدهند. هنوز `SEO_AGENT_TITLE_WORKFLOW_ENABLED` را
روشن نکن؛ مسیر عنوان به منبع SERP هم نیاز دارد.
