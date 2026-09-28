# 🚌 Smart Bus Tracking System – AWS Cloud Deployment

A web-based Smart Bus Tracking System developed using **Python, Flask, MySQL, and AWS**.

## 📌 Overview

This project demonstrates the deployment of a Flask web application on AWS with a cloud-hosted MySQL database, backup storage, and monitoring.

> **Note:** The current implementation displays bus information stored in the database. Real-time GPS tracking is not implemented.

## ☁️ AWS Services

- **Amazon EC2** – Hosts the Flask application
- **Amazon RDS** – MySQL database
- **Amazon S3** – Database backup storage
- **Amazon CloudWatch** – Resource monitoring

## 🛠️ Technologies

- Python
- Flask
- MySQL
- HTML & CSS
- AWS

## ✨ Features

- User login
- Bus information management
- MySQL database integration
- AWS cloud deployment
- Database backup to Amazon S3
- EC2 monitoring with CloudWatch

## 📂 Project Structure

```text
SmartBus-AWS-Deployment/
├── static/
├── templates/
├── app.py
├── create_db.py
├── schema.sql
├── requirements.txt
├── .env.example
└── .gitignore

🚀 Architecture

User → EC2 (Flask) → RDS (MySQL)
** ↓**
** S3 (Backup)**
** ↓**
** CloudWatch (Monitoring)**

🎯 Future Enhancements
Real-time GPS tracking
Live bus location updates
Route visualization
Estimated Time of Arrival (ETA)
User notifications
