# 数学分析公开回归样本源稿

本文件与 `source.pdf` 由 Axiom-Flow 项目自行创作，用于解析回归，不摘录任何教材。

## 第 1 页

# Mathematical Analysis Fixture

数学分析回归样本：本页验证中英文正文、标题层级与行内公式。

For a sequence $(a_n)$, convergence to $L$ means every epsilon admits a tail bound.

$$\lim_{n \to \infty} a_n = L$$

## 第 2 页

# Theorem and Proof

## Theorem

If $f$ is differentiable at $x_0$, then $f$ is continuous at $x_0$.

## Proof

Write $f(x)-f(x_0)$ as a difference quotient times $x-x_0$ and take the limit.

1. Isolate the difference quotient.
2. Apply the product limit law.

$$f(x)-f(x_0)=\frac{f(x)-f(x_0)}{x-x_0}(x-x_0)$$

## 第 3 页

# Table and Figure

The table records a convergent sequence.

| n | 1 | 2 | 4 | 8 |
| --- | --- | --- | --- | --- |
| 1/n | 1 | 0.5 | 0.25 | 0.125 |

Figure 1. Secant slopes approaching the tangent line.

## 第 4 页

# Two-column Reading Order

## Left column

Definition. A set is open when every point has a contained neighborhood.

Example. Every open interval $(a,b)$ is open in the real line.

## Right column

Definition. A set is closed when it contains all of its limit points.

Observation. Complements exchange open and closed sets.
