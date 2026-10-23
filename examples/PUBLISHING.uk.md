# Завантаження на GitHub

1. У GitHub створіть новий репозиторій **dtek-outage-card**. Для встановлення іншими користувачами через HACS зробіть його публічним.
2. Розпакуйте архів. Завантажте **вміст папки**, а не саму папку чи ZIP: `README.md`, `hacs.json` і `dtek-outage-card.js` мають бути в корені GitHub-репозиторію. Через Git збережуться також приховані файли й `.github`.
3. За бажанням використайте команди нижче, підставивши свій GitHub-логін. Виконуйте з розпакованої папки:

   ```sh
   git init
   git add .
   git commit -m "Initial release: DTEK Outage Card"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/dtek-outage-card.git
   git push -u origin main
   ```

4. Дочекайтеся GitHub Actions **Check card**. Він перевірить синтаксис і браузерні сценарії. Для нового репозиторію може знадобитися увімкнути Actions у його налаштуваннях.
5. У **Releases → Draft a new release** створіть тег `v1.0.0`, назву `DTEK Outage Card 1.0.0`, вставте зміни з CHANGELOG.md і прикріпіть `dtek-outage-card.js`. Опублікуйте реліз.
6. Додайте URL репозиторію у HACS → Custom repositories → **Dashboard**.

Подання в загальний каталог HACS не виконується автоматично. Цей пакет підготовлено для custom repository; фактична перевірка HACS можлива після розміщення на GitHub.

При оновленні змініть версію у package.json, package-lock.json і CHANGELOG.md; створіть наступний реліз. За ручного встановлення оновіть JS у `/config/www/` і параметр версії ресурсу.
